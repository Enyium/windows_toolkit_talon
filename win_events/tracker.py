from collections import deque
import ctypes
import math
from threading import Condition
import time
from types import TracebackType
from typing import Literal, Optional, Self, Sequence, TYPE_CHECKING, Union
from uuid import UUID

from talon import app

from .. import reload_resilience

if app.platform == "windows" or TYPE_CHECKING:
    import pythoncom
    import win32com.client
    import win32con
    import winerror

    from ..lib.winapi import CType, kernel32, oleacc, user32, wapi
    from .listener import WinEventListener
    from .constants import WinEvent, Role
else:
    raise NotImplementedError("Unsupported OS.")

_script_main_callbacks = deque()

def _script_main():
    while _script_main_callbacks:
        _script_main_callbacks.popleft()()


class WinEventTracker:
    """Tracks win events. Among other things, this allows you to wait for their occurrence to in turn be able to continue UI automation at the appropriate time.

    - See also docs for the `SetWinEventHook()` WinAPI function.
    - You can use Microsoft's AccEvent in "WinEvents (Out of Context)" mode to find utilizable events and other attributes for your use case. (<https://learn.microsoft.com/en-us/windows/win32/winauto/accessible-event-watcher>)
    """

    #i If useful, another class `WinEventTrackerGroup` could be implemented with a constructor that takes any number of `WinEventTracker`s (with their own filter criteria) and that provides the same public methods. `WinEventTracker` would get an additional field `_shared_condition` that it notified and that `WinEventTrackerGroup` waited on. `WinEventTracker` would provide additional non-public methods that allowed `WinEventTrackerGroup` to check each `WinEventTracker` without waiting (logic currently in waiting methods). It's a kind of polling with intelligent waits told by the single `WinEventTracker`s (smallest waiting request wins). (Perhaps useful for implementation: `contextlib.ExitStack`.)
    #i
    #i `_shared_condition` needs to be accompanied by a flag to avoid lost wakeups, like so:
    #i     # In win event thread after regular-`Condition` context.
    #i     with self._shared_condition:
    #i         self._shared_condition_notified_cell.value = True
    #i         self._shared_condition.notify_all()
    #i     
    #i     # In regular thread.
    #i     while True:
    #i         ...
    #i     
    #i         with self._shared_condition:
    #i             while not self._shared_condition_notified_cell.value:
    #i                 timed_out = not self._shared_condition.wait(timeout)
    #i                 #> Use `timed_out`.
    #i             self._shared_condition_notified_cell.value = False

    @classmethod
    def _main(cls):
        listener_uuid4 = UUID("8b5e0d5c-629c-4f1b-9d29-a225b47a5529")
        try:
            old_listener = reload_resilience.pop_value(listener_uuid4)
            if old_listener:
                old_listener.shut_down(wait=False)
        except (KeyError, AttributeError, TypeError):  # Accounts for API changes.
            print(f"WARNING: The `{WinEventTracker.__name__}` class couldn't shut down its last `{WinEventListener.__name__}` (expected on Talon launch).")

        cls.__listener = WinEventListener(listener_uuid4)
        reload_resilience.set_value(listener_uuid4, cls.__listener)

    def __init__(
        self,
        events: Union[
            WinEvent,
            slice,
            Sequence[Union[WinEvent, slice]],
        ],
        *,
        object_id: Optional[int] = None,
        object_id_is_custom: Optional[bool] = None,
        target_is_object_itself: Optional[bool] = None,
        role: Optional[Role] = None,
        inclusive_ancestor_hwnd: Optional[int] = None,
        process_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Creates an instance that filters win events according to the arguments and then lets you wait for a subset of events.

        - `events` specifies single events and/or inclusive ranges of events.
        - `object_id` accepts an `ObjectID` member or a custom ID found out by spying.
        - `object_id_is_custom` specifies whether the object ID is not one of the predefined values.
        - `target_is_object_itself` corresponds to `CHILDID_SELF` from Microsoft's docs.
        - `role` is ignored for the events `OBJECT_CREATE` and `OBJECT_DESTROY`.
        - `timeout` may be overridden by a waiting method.
        """

        # Hook filters.
        if isinstance(events, (WinEvent, slice)):
            events = (events,)
        for event_or_slice in events:
            if isinstance(event_or_slice, slice):
                WinEventListener._verify_event_slice(event_or_slice)
        self.__events = events

        self.__process_id = process_id
        self.__thread_id = thread_id

        # Own filters.
        self.__object_id = object_id
        self.__object_id_is_custom = object_id_is_custom
        self.__target_is_object_itself = target_is_object_itself
        self.__role = role

        self.__inclusive_ancestor_hwnd = (
            wapi.cast("HWND", inclusive_ancestor_hwnd)
            if inclusive_ancestor_hwnd is not None
            else None
        )
        #i The event load can't be reliably reduced using the window's thread ID, because the window might not belong to same process or thread as the win event producer: Browsers have separate processes, UWP apps have a host process.

        #
        self.__entered = False
        self.__subscription_handles = []
        self.__timeout = timeout

        self.__num_events = 0
        """The overall number of win events received to guard against reentrancy."""
        self.__condition = Condition()
        self.__times_by_events: dict[int, float] = {}  # `int` because of unknown events.
        self.__num_events_by_events: dict[int, int] = {}
        """The overall number of win events when the corresponding event time was saved to guard against reentrancy."""

    def __enter__(self) -> Self:
        """Starts listening for the instance's win events."""

        if self.__entered:
            raise RuntimeError("Already entered.")

        self.__entered = True

        for event_or_slice in self.__events:
            subscription_handle = self.__listener.subscribe(
                event_or_slice,
                self.__process_id,
                self.__thread_id,
                on_winevent=self.__on_winevent,
            )
            self.__subscription_handles.append(subscription_handle)

        now = time.perf_counter()
        self.__waiting_start_time = now
        self.__latest_event_time_at_last_had_call = math.nextafter(now, -math.inf)

        return self

    def __on_winevent(
        self,
        event: int,
        hwnd: CType,
        object_id: int,
        child_id: int,
        thread_id: int,
        time_ms: int,
    ) -> None:
        #i Certain WinAPI functions may cause this event handler to be reentered during their call. See <https://learn.microsoft.com/en-us/windows/win32/winauto/guarding-against-reentrancy-in-hook-functions>.

        event_reception_time = time.perf_counter()
        #i Event generation time is too imprecise (16-ms time windows for author).

        self.__num_events += 1
        num_events = self.__num_events

        if self.__object_id is not None and object_id != self.__object_id:
            return

        if self.__object_id_is_custom is not None:
            object_id_is_custom = object_id > 0
            if object_id_is_custom != self.__object_id_is_custom:
                return

        if self.__target_is_object_itself is not None:
            target_is_object_itself = child_id == win32con.CHILDID_SELF
            if target_is_object_itself != self.__target_is_object_itself:
                return

        if self.__inclusive_ancestor_hwnd is not None:
            kernel32.SetLastError(winerror.ERROR_SUCCESS)
            is_child = user32.IsChild(self.__inclusive_ancestor_hwnd, hwnd)
            if not is_child:
                last_error = kernel32.GetLastError()
                if last_error != winerror.ERROR_SUCCESS:
                    raise ctypes.WinError(last_error)

            if not (hwnd == self.__inclusive_ancestor_hwnd or is_child):
                return

        if (
            event != WinEvent.OBJECT_CREATE
            and event != WinEvent.OBJECT_DESTROY
            #i Event exclusions mandated by `AccessibleObjectFromEvent()` docs.
            and self.__role is not None
        ):
            #i This if-body may cause the event handler to be reentered.

            iaccessible_address = wapi.new("void **")
            acc_object_child_id_cffi_variant = wapi.new("VARIANT *")
            hresult = oleacc.AccessibleObjectFromEvent(
                hwnd,
                wapi.cast("DWORD", object_id),
                wapi.cast("DWORD", child_id),
                iaccessible_address,
                acc_object_child_id_cffi_variant,
            )

            if hresult < 0:
                # if hresult == winerror.E_INVALIDARG:
                #     print(f"ERROR: `AccessibleObjectFromEvent()` call ended with error HRESULT 0x{hresult & 0xFFFF_FFFF:08X}. Arguments: hwnd = {hwnd}, object_id = {object_id}, child_id = {child_id}. event = {event}.")

                if hresult == winerror.E_INVALIDARG or hresult == winerror.E_FAIL:
                #i Errors that were encountered, but didn't appear to have a clear, avoidable cause.
                    return  # Filters can't match.
                else:
                    raise ctypes.WinError(hresult)
            else:
                acc_object = win32com.client.Dispatch(
                    pythoncom.ObjectFromAddress(
                        int(wapi.cast("uintptr_t", iaccessible_address[0])),
                        pythoncom.IID_IDispatch,
                    )
                )

                #i For the case that the `IAccessible` API doesn't work, AI recommended `win32com.client.gencache.EnsureDispatch()` instead of `win32com.client.Dispatch()`. Although this perhaps takes a moment on first run and is subject to the problems mentioned below.
                #i
                #i To learn more about the `IAccessible` API, look into `%TEMP%\gen_py\3.11\1EA4DBF0-3C3B-11CF-810C-00AA00389B71x0x1x1.py`. To generate the file, run the code told to you by doing the following in the Talon REPL:
                #i     import sys
                #i     from win32com.client import makepy
                #i     sys.argv = "dummy -i oleacc.dll".split(" ")
                #i     #i See `%ProgramFiles%\Talon\Lib\site-packages\win32com\client\makepy.py` for more switches.
                #i     makepy.main()
                #i Make sure to move the generated file to an ineffective directory, so changes like Talon and thus pywin32 updates don't introduce conflicts.

                acc_object_child_id_cffi_variant_type = acc_object_child_id_cffi_variant._VARIANT_NAME_1._VARIANT_NAME_2.vt
                if acc_object_child_id_cffi_variant_type != pythoncom.VT_I4:
                    raise RuntimeError(f"Unexpected variant type {acc_object_child_id_cffi_variant_type} of accessible object's child ID.")
                acc_object_child_id_variant = win32com.client.VARIANT(
                    pythoncom.VT_I4,
                    acc_object_child_id_cffi_variant._VARIANT_NAME_1._VARIANT_NAME_2._VARIANT_NAME_3.lVal,
                )

                if self.__role is not None:
                    role: int = acc_object.GetaccRole(acc_object_child_id_variant)
                    if role != self.__role:
                        return

            if self.__num_events_by_events.get(event, 0) > num_events:
                # Forget this event, because its now old.
                return

        # All filters matched, and it's the most recent event. Save it.
        with self.__condition:
            self.__times_by_events[event] = event_reception_time
            self.__num_events_by_events[event] = num_events
            self.__condition.notify_all()

    def reset_wait_start(self):
        """Sets the start time for the next `wait()` call to now."""

        self.__waiting_start_time = time.perf_counter()

    def wait(
        self,
        events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """Waits until one of the specified events occurs since entering the context manager or the last `reset_wait_start()` call.

        If the instance was created with just one event, `events` can be omitted.

        Returns `False` on timeout (given argument or default specified at instance creation).
        """

        if not self.__entered:
            raise RuntimeError("Cannot wait for win events without entering context manager first.")

        if timeout is None:
            timeout = self.__timeout
        deadline = time.perf_counter() + timeout if timeout is not None else None

        events = self.__treat_events(events)

        with self.__condition:
            while True:
                for event in events:
                    event_time = self.__times_by_events.get(event)
                    if event_time is not None and event_time >= self.__waiting_start_time:
                        return True

                duration_until_deadline = (
                    max(0, deadline - time.perf_counter())
                    if deadline is not None
                    else None
                )

                timed_out = not self.__condition.wait(duration_until_deadline)
                if timed_out:
                    return False

        assert False

    def require(
        self,
        events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Like `wait()`, but raises a `TimeoutError`."""

        events = self.__treat_events(events)

        in_time = self.wait(events, timeout)
        if not in_time:
            raise TimeoutError(f"None of the win events from `{events!r}` occurred before timeout.")

    def wait_for_silence(
        self,
        silence_duration: float,
        events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None,
    ) -> bool:
        """Waits until none of the specified events occurred for the silence duration.

        - The event delivery latency is included in the silence duration. Expect at least 21 ms.
        - If the instance was created with just one event, `events` can be omitted.

        Returns `False` on timeout (specified at instance creation). A timeout-related return may happen before the timeout is reached if there's no chance to fulfill the silence requirement before the deadline.
        """

        if not self.__entered:
            raise RuntimeError("Cannot wait for win events without entering context manager first.")

        start_time = time.perf_counter()
        deadline = start_time + self.__timeout if self.__timeout is not None else None

        events = self.__treat_events(events)

        with self.__condition:
            while True:
                latest_event_time = start_time
                for event in events:
                    event_time = self.__times_by_events.get(event)
                    if event_time is not None and event_time > latest_event_time:
                        latest_event_time = event_time

                now = time.perf_counter()
                past_silence_duration = now - latest_event_time
                remaining_silence_duration = max(0, silence_duration - past_silence_duration)

                if deadline is not None and now + remaining_silence_duration > deadline:
                    return False

                silence_elapsed = not self.__condition.wait(remaining_silence_duration)
                if silence_elapsed:
                    return True

        assert False

    def require_silence(
        self,
        silence_duration: float,
        events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None,
    ) -> None:
        """Like `wait_for_silence()`, but raises a `TimeoutError`."""

        events = self.__treat_events(events)

        in_time = self.wait_for_silence(silence_duration, events)
        if not in_time:
            raise TimeoutError(f"The win events `{events!r}` didn't become silent before timeout.")

    def had(self, events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None) -> bool:
        """Returns whether one of the specified events occurred since the context manager was entered or this method was last called.

        If the instance was created with just one event, `events` can be omitted.
        """

        events = self.__treat_events(events)

        result = False
        with self.__condition:
            for event in events:
                event_time = self.__times_by_events.get(event)
                if event_time is not None and event_time > self.__latest_event_time_at_last_had_call:
                    result = True
                    self.__latest_event_time_at_last_had_call = event_time

        return result

    def __treat_events(self, events: Optional[Union[WinEvent, Sequence[WinEvent]]] = None) -> Sequence[WinEvent]:
        if events is not None:
            if isinstance(events, WinEvent):
                events = (events,)
        elif len(self.__events) == 1 and isinstance(self.__events[0], WinEvent):
            events = self.__events
        else:
            raise ValueError("Win events are only optional when the instance was created with exactly one.")

        return events

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[Literal[True]]:
        """Stops listening for win events."""

        if not self.__entered:
            raise RuntimeError("Exit without enter.")

        self.__entered = False

        exceptions = None
        for subscription_handle in self.__subscription_handles:
            try:
                self.__listener.unsubscribe(subscription_handle)
            except Exception as e:
                if not exceptions:
                    exceptions = []
                exceptions.append(e)

        if exceptions:
            if len(exceptions) == 1:
                raise exceptions[0]
            else:
                raise ExceptionGroup("Multiple exceptions while unsubscribing from win events.", exceptions)


_script_main_callbacks.append(WinEventTracker._main)

_script_main()
