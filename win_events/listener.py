import functools
import textwrap
import threading
from threading import Event, RLock
import traceback
from typing import Optional, Protocol, TYPE_CHECKING, Union
from uuid import UUID

from talon import app

from ..lib.weak import WeakCallback, to_weak_callback
from .constants import WinEvent

if app.platform == "windows" or TYPE_CHECKING:
    import win32con

    from ..lib.message_loop import MessageLoopExecutor
    from ..lib.winapi import CData, user32, wapi
else:
    raise NotImplementedError("Unsupported OS.")


class WinEventListener:
    """Lets you subscribe to win events to receive them in a separate thread."""

    class OnWinEventCallback(Protocol):
        def __call__(
            self,  # Just belongs to `Protocol`.
            subscription_handle: int,
            event: int,
            hwnd: CData,
            object_id: int,
            child_id: int,
            thread_id: int,
            time_ms: int,
        ) -> None:
            """See docs for the `WINEVENTPROC` WinAPI callback.

            `time_ms` is compatible with the `GetTickCount()` WinAPI function. Both wrap around to zero after `0xFFFF_FFFF`. The granularity of these values is relatively coarse.
            """
            ...

    def __init__(self, instance_uuid4: UUID) -> None:
        self.__label = f'`{WinEventListener.__name__}` with UUID "{instance_uuid4}"'
        self.__lock = RLock()
        self.__is_shut_down_event = Event()
        self.__weak_callbacks_by_hook_handles: dict[CData, WeakCallback[WinEventListener.OnWinEventCallback]] = {}

        self.__executor = MessageLoopExecutor(
            instance_uuid4,
            on_thread_created=self.__on_thread_created,
            on_thread_exit=functools.partial(
                WinEventListener.__on_thread_exit,
                self.__lock,
                self.__is_shut_down_event,
                self.__weak_callbacks_by_hook_handles,
            ),
        )

    def __on_thread_created(self) -> None:
        tls = WinEventListener.__tls = threading.local()
        #i Thread-local storage that is only used in the win event procedure to compensate for its missing `self` parameter. Since a class instance gets its own message loop thread, using TLS is as good as `self`.

        tls.label = self.__label
        tls.lock = self.__lock
        tls.is_shut_down_event = self.__is_shut_down_event
        tls.weak_callbacks_by_hook_handles = self.__weak_callbacks_by_hook_handles

    def subscribe(
        self,
        events: Union[WinEvent, slice],
        process_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        *,
        on_winevent: OnWinEventCallback,
    ) -> int:
        """Starts calling the callback whenever the win event or one from the inclusive range of win events occurred.

        - If `process_id` or `thread_id` is `None`, the respective filter is disabled.
        - `on_winevent()` is called in a separate thread. Calling certain WinAPI functions may cause reentrancy; see <https://learn.microsoft.com/en-us/windows/win32/winauto/guarding-against-reentrancy-in-hook-functions>.

        Returns a subscription handle that you need to unsubscribe.

        See also docs for the `SetWinEventHook()` WinAPI function.
        """

        hook_handle = self.__executor.invoke(self.__set_hook, events, process_id, thread_id, on_winevent, timeout=2)
        return int(wapi.cast("uintptr_t", hook_handle))

    def __set_hook(
        self,
        events: Union[WinEvent, slice],
        process_id: Optional[int],
        thread_id: Optional[int],
        on_winevent: OnWinEventCallback,
    ) -> CData:
        with self.__lock:
            if self.__is_shut_down_event.is_set():
                raise RuntimeError(f"{self.__label} already shut down.")

            if isinstance(events, slice):
                WinEventListener._verify_event_slice(events)
                first_event, last_event = events.start, events.stop
            else:
                first_event = last_event = events

            hook_handle = user32.SetWinEventHook(
                first_event,
                last_event,
                wapi.NULL,  # Not required with `WINEVENT_OUTOFCONTEXT`.
                WinEventListener.__winevent_proc,
                process_id or 0,
                thread_id or 0,
                win32con.WINEVENT_OUTOFCONTEXT | win32con.WINEVENT_SKIPOWNTHREAD,  # Skip message loop thread.
            )
            if not hook_handle:
                raise RuntimeError(f"{self.__label} failed to hook into win events `{first_event}` through `{last_event}`.")

            self.__weak_callbacks_by_hook_handles[hook_handle] = to_weak_callback(on_winevent)
            return hook_handle

    def _verify_event_slice(event_range: slice) -> None:
        if not (
            isinstance(event_range.start, WinEvent)
            and isinstance(event_range.stop, WinEvent)
        ):
            raise ValueError(f"Win event range bounds must be of type `{WinEvent.__name__}`.")

        if event_range.step not in {None, 1}:
            raise ValueError("Step sizes aren't supported for win event ranges.")

    def unsubscribe(self, subscription_handle: int) -> None:
        """Voids the specified subscription, as if you never subscribed. This doesn't affect any subscriptions with overlapping event ranges. (You shouldn't subscribe to overlapping ranges.)"""

        self.__executor.invoke(self.__unhook, wapi.cast("HWINEVENTHOOK", subscription_handle), timeout=2)

    def __unhook(self, hook_handle: CData) -> None:
        with self.__lock:
            if self.__is_shut_down_event.is_set():
                raise RuntimeError(f"{self.__label} already shut down.")

            try:
                del self.__weak_callbacks_by_hook_handles[hook_handle]
            except KeyError:
                raise ValueError(f"{self.__label} couldn't find hook handle `{hook_handle}` among its registered hooks.")

            success = user32.UnhookWinEvent(hook_handle)
            if not success:
                raise RuntimeError(f"{self.__label} failed to unhook from win events with hook handle `{hook_handle}`.")

    @wapi.callback("BARE_WINEVENTPROC")
    @staticmethod
    def __winevent_proc(
        hWinEventHook: CData,
        event: int,
        hwnd: CData,
        idObject: int,
        idChild: int,
        idEventThread: int,
        dwmsEventTime: int,
    ) -> None:
        try:
            tls = WinEventListener.__tls

            with tls.lock:
                if tls.is_shut_down_event.is_set():
                    return

                try:
                    weak_callback = tls.weak_callbacks_by_hook_handles[hWinEventHook]
                except KeyError:
                    raise RuntimeError(f"{tls.label} received win event 0x{event:04X} with unknown hook handle `{hWinEventHook}`.")

            callback = weak_callback()
            if callback:
                subscription_handle = int(wapi.cast("uintptr_t", hWinEventHook))
                callback(subscription_handle, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime)
            else:
                success = user32.UnhookWinEvent(hWinEventHook)
                if not success:
                    raise RuntimeError(f"{tls.label} failed to unhook from win events with hook handle `{hWinEventHook}` after callback became unavailable.")
        except BaseException:
            print(
                f"ERROR: Unhandled exception in win event handler of {tls.label}:\n"
                + textwrap.indent(traceback.format_exc().rstrip(), "  ")
            )

    def shut_down(self, wait: bool = True) -> None:
        with self.__lock:
            if self.__is_shut_down_event.is_set():
                return

            self.__is_shut_down_event.set()

            unhook_futures = []
            for hook_handle in reversed(self.__weak_callbacks_by_hook_handles.keys()):
                future = self.__executor.submit(self.__unhook, hook_handle)
                if wait:
                    unhook_futures.append(future)

        unhook_exceptions = None
        if wait:
            for future in unhook_futures:
                try:
                    future.result(timeout=2)
                except Exception as e:
                    if not unhook_exceptions:
                        unhook_exceptions = []
                    unhook_exceptions.append(e)

        self.__executor.shutdown(wait)

        if unhook_exceptions:
            if len(unhook_exceptions) == 1:
                raise unhook_exceptions[0]
            else:
                raise ExceptionGroup("Couldn't remove multiple win event hooks.", unhook_exceptions)
        #i Last because `...Executor` exceptions are regarded as more fundamental.

    @staticmethod
    def __on_thread_exit(
        lock: RLock,
        is_shut_down_event: Event,
        weak_callbacks_by_hook_handles: dict[CData, WeakCallback[OnWinEventCallback]],
    ) -> None:
        with lock:
            if is_shut_down_event.is_set():
                return

            is_shut_down_event.set()

            for hook_handle in reversed(weak_callbacks_by_hook_handles.keys()):
                success = user32.UnhookWinEvent(hook_handle)
                if not success:
                    print(f"WARNING: Couldn't remove win event hook with handle `{hook_handle}`.")

            weak_callbacks_by_hook_handles.clear()
