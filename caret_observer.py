import ctypes
from dataclasses import dataclass
from enum import auto, IntEnum
import threading
from threading import Condition, Event, Thread
import time
from typing import Optional, TYPE_CHECKING

from talon import app, Module

if app.platform == "windows" or TYPE_CHECKING:
    import pywintypes
    import win32api
    import win32con
    import win32process
    import winerror

    from .lib.winapi import kernel32, user32, wapi

    CType = wapi.CType
else:
    raise NotImplementedError("Unsupported OS.")

_mod = Module()

caret_observer = None

def _script_main():
    global caret_observer
    caret_observer = _CaretObserver()


@_mod.action_class
class _Actions:
    def smart_input_test_caret_observer():
        """Test for the `_CaretObserver` class. Try inaction and postponing completion using caret movements."""

        caret_observer.observe()

        start = time.perf_counter()
        caret_observer.wait_for_standstill(1, 30)
        print(f"Duration until caret standstill: {time.perf_counter() - start:.3f} s")

        start = time.perf_counter()
        caret_observer.wait_for_standstill(1, 30)
        print(f"2nd duration until caret standstill: {time.perf_counter() - start:.3f} s")

        caret_observer.stop_observing()


class _CaretObserver:
    """Observes caret movement in a separate thread and lets you wait until it has stopped moving for a certain amount of time."""

    _tls = threading.local()
    """Thread-local storage that is only used in the win event callback to compensate for its missing `self` parameter. Since a class instance creates its own thread, using TLS is as good as `self`."""

    class _Command(IntEnum):
        OBSERVE = auto()
        STOP_OBSERVING = auto()

    def __init__(self):
        self._thread_comm_message_id = user32.RegisterWindowMessageW("Talon.SmartInput.CaretObserver.ThreadComm")
        if not self._thread_comm_message_id:
            raise ctypes.WinError(kernel32.GetLastError())

        # Stop old thread from before script reload.
        from . import reload_resilience
        (old_native_thread_id, old_thread_creation_time) = reload_resilience.caret_observer_thread_info_backup
        if old_native_thread_id:
            self._quit_thread(old_native_thread_id, old_thread_creation_time)

        # Start new thread.
        self._thread_ready = Event()

        self._thread = Thread(target=self._run_thread, name="Caret Observer", daemon=True)
        self._thread.start()
        reload_resilience.caret_observer_thread_info_backup = (self._thread.native_id, _get_thread_creation_time(self._thread.native_id))

        # Ensure thread is ready before allowing to work with the object. (This allows to continue initialization in thread.)
        success = self._thread_ready.wait(3)
        if not success:
            raise RuntimeError("Caret observer thread couldn't be set up before timeout.")

    def _run_thread(self):
        self._comm_condition = self._tls.comm_condition = Condition()  # Communication `Condition`.
        self._num_commands_pending = 0
        self._boxed_winevent_hook_handle = self._tls.boxed_winevent_hook_handle = _Box()  # `None` means not observing.
        self._boxed_last_caret_move_time_ms = self._tls.boxed_last_caret_move_time_ms = _Box()

        msg = wapi.new("MSG *")
        user32.PeekMessageW(msg, wapi.NULL, 0, 0, user32.PM_NOREMOVE)
        #i "The functions that are guaranteed to create a message queue are `Peek­Message`, `Get­Message`, and `Create­Window`." (<https://devblogs.microsoft.com/oldnewthing/20241009-00/?p=110354>) But `GetMessageW()` blocks.

        #. Main thread may finish initializing instance.
        self._thread_ready.set()

        while True:
            result = user32.GetMessageW(msg, wapi.NULL, 0, 0)
            if result == -1:
                raise ctypes.WinError(kernel32.GetLastError())
            #i `GetMessageW()` calls the `SetWinEventHook()` callback without returning.

            if msg.hwnd:
                # Not a thread message. (Shouldn't happen.)
                continue

            if msg.message == user32.WM_QUIT:
                self._stop_observing_concurrently()
                break

            if msg.message != self._thread_comm_message_id:
                continue
            match msg.wParam:
                case self._Command.OBSERVE:
                    self._observe_concurrently()
                case self._Command.STOP_OBSERVING:
                    self._stop_observing_concurrently()

    def observe(self):
        """Starts a new observing session."""

        with self._comm_condition:
            self._num_commands_pending += 1
            success = user32.PostThreadMessageW(self._thread.native_id, self._thread_comm_message_id, self._Command.OBSERVE, 0)
            if not success:
                raise ctypes.WinError(kernel32.GetLastError())

    def _observe_concurrently(self):
        """Called in separate thread."""

        with self._comm_condition:
            self._num_commands_pending -= 1

            if self._boxed_winevent_hook_handle.value is None:
                winevent_hook_handle = user32.SetWinEventHook(
                    user32.EVENT_OBJECT_LOCATIONCHANGE,
                    user32.EVENT_OBJECT_LOCATIONCHANGE,
                    #i In Windows Terminal, both `EVENT_OBJECT_LOCATIONCHANGE` and `EVENT_CONSOLE_CARET` didn't work. In `conhost.exe`, they did. (Windows 11 Home 24H2)
                    wapi.NULL,
                    self._winevent_proc,
                    0,  # Top-level `HWND` might not belong to same process...
                    0,  # ...or thread as event producer. Browsers have separate processes; UWP apps have a host process.
                    user32.WINEVENT_OUTOFCONTEXT | user32.WINEVENT_SKIPOWNTHREAD,
                )
                if not winevent_hook_handle:
                    raise RuntimeError("Couldn't hook into win events.")

                self._boxed_winevent_hook_handle.value = winevent_hook_handle
                self._boxed_last_caret_move_time_ms.value = None

            self._comm_condition.notify_all()

    def stop_observing(self):
        """Stops the observing session."""

        with self._comm_condition:
            self._num_commands_pending += 1
            success = user32.PostThreadMessageW(self._thread.native_id, self._thread_comm_message_id, self._Command.STOP_OBSERVING, 0)
            if not success:
                raise ctypes.WinError(kernel32.GetLastError())

    def _stop_observing_concurrently(self):
        """Called in separate thread."""

        with self._comm_condition:
            self._num_commands_pending -= 1

            if self._boxed_winevent_hook_handle.value is not None:
                try:
                    success = user32.UnhookWinEvent(self._boxed_winevent_hook_handle.value)
                    if not success:
                        raise RuntimeError("Couldn't unhook from win events.")
                finally:
                    self._boxed_winevent_hook_handle.value = None

            self._comm_condition.notify_all()

    @staticmethod
    @wapi.callback("BARE_WINEVENTPROC")
    def _winevent_proc(
        hWinEventHook: CType,
        event: int,
        hwnd: CType,
        idObject: int,
        idChild: int,
        idEventThread: int,
        dwmsEventTime: int,
    ):
        """Called in separate thread."""

        #i Calling certain functions causes reentrancy, which could be bad. See <https://learn.microsoft.com/en-us/windows/win32/winauto/guarding-against-reentrancy-in-hook-functions>.

        #. Fastest possible return for objects other than carets, because `EVENT_OBJECT_LOCATIONCHANGE` also reports every mouse movement and more.
        if idObject != user32.OBJID_CARET:
            return

        tls = _CaretObserver._tls
        with tls.comm_condition:
            if tls.boxed_winevent_hook_handle.value is None:
                return

            if event == user32.EVENT_OBJECT_LOCATIONCHANGE:
                tls.boxed_last_caret_move_time_ms.value = dwmsEventTime
                #i "Events are guaranteed to be in sequential order." (`SetWinEventHook()` docs) So, as long as reentrancy doesn't mix execution up (see above), the current `dwmsEventTime` should semantically always be ahead, even if numerically smaller than the last value because of wrap-around.
                tls.comm_condition.notify_all()

    def wait_for_standstill(self, standstill_duration: float, max_waiting_duration: float):
        """Waits until no caret movement was recognized for the specified amount of time, at which point the caret is assumed as stable. An observing session must be active. After the maximum waiting duration, an exception is raised.

        Note that apps' UIs may visually lag, appearing to smooth out the actually received timestamps of caret move events.
        """

        ms_until_caret_standstill = round(standstill_duration * 1000)
        outer_waiting_start_time = time.perf_counter()

        with self._comm_condition:
            self._boxed_last_caret_move_time_ms.value = None

            timed_out = not self._comm_condition.wait_for(
                lambda: (
                    self._num_commands_pending <= 0
                    and self._boxed_winevent_hook_handle.value is not None
                ),
                2,
            )
            if timed_out:
                raise RuntimeError("Caret observer thread didn't start observing before timeout.")
            assert not self._num_commands_pending < 0

            start_time_ms = kernel32.GetTickCount()

            while True:
                now_ms = kernel32.GetTickCount()

                last_caret_move_time_ms = self._boxed_last_caret_move_time_ms.value
                reference_ms = last_caret_move_time_ms if last_caret_move_time_ms is not None else start_time_ms

                ms_since_reference = (now_ms - reference_ms) & 0xFFFF_FFFF
                #i These timestamps wrap around. Bitmask makes it positive again, while maintaining the correct magnitude.
                ms_to_wait = max(0, ms_until_caret_standstill - ms_since_reference)

                timed_out = not self._comm_condition.wait(ms_to_wait / 1000)
                if timed_out:
                    break

                if time.perf_counter() - outer_waiting_start_time > max_waiting_duration:
                    raise RuntimeError("Waited longer than the maximum duration for caret standstill.")

    def _quit_thread(self, native_thread_id: Optional[int] = None, creation_time: Optional[pywintypes.TimeType] = None):
        """Pass the arguments to terminate an old thread. The method will be a no-op if the thread was already terminated or a thread with another creation time was assigned the same ID."""

        if (native_thread_id is None) != (creation_time is None):
            raise ValueError("Specify no arguments or both.")

        if native_thread_id is None:
            native_thread_id = self._thread.native_id

        if creation_time is not None:
            try:
                actual_creation_time = _get_thread_creation_time(native_thread_id)
                if actual_creation_time != creation_time:
                    # ID reused for foreign thread.
                    return
            except OSError as e:
                if (
                    e.winerror == winerror.ERROR_INVALID_THREAD_ID  # Already terminated.
                    or e.winerror == winerror.ERROR_ACCESS_DENIED  # ID reused for privileged thread.
                ):
                    return
                else:
                    raise

        # Terminate thread established as our own.
        success = user32.PostThreadMessageW(native_thread_id, user32.WM_QUIT, 0, 0)
        if not success:
            last_error = kernel32.GetLastError()
            if last_error == winerror.ERROR_INVALID_THREAD_ID:
                # Already terminated.
                pass
            else:
                raise ctypes.WinError(last_error)

    def __del__(self):
        self._quit_thread()
        #i The `Thread`s `daemon` flag will have to suffice when terminating Talon, since Talon v0.4.0 doesn't seem to provide a working event for that case. This finalizer is just for unforeseen cases, like perhaps the `reload_resilience` module being reloaded.


@dataclass
class _Box:
    """Makes it possible to access the same immutable data from multiple sources."""
    value: Optional[object] = None


def _get_thread_creation_time(native_thread_id: int) -> pywintypes.TimeType:
    thread_handle = win32api.OpenThread(win32con.THREAD_QUERY_LIMITED_INFORMATION, False, native_thread_id)
    creation_time = win32process.GetThreadTimes(thread_handle)["CreationTime"]
    thread_handle.Close()
    return creation_time

_script_main()
