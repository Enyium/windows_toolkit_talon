"""
Classes to get a Win32 message loop.
"""

from collections import deque
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, Executor, Future, InvalidStateError
import ctypes
import functools
import textwrap
from threading import Event, Lock, Thread
import traceback
from typing import Any, cast, ParamSpec, TYPE_CHECKING, TypeAlias, TypeVar
from uuid import UUID
import weakref
from weakref import ReferenceType

from talon import app

from ..pymod_termination.index import get_pymod_termination_hook
from .weak import call_weak, to_weak_callback

T = TypeVar("T")
P = ParamSpec("P")

if app.platform == "windows" or TYPE_CHECKING:
    import winerror

    from .winapi import kernel32, user32, wapi
else:
    raise NotImplementedError("Unsupported OS.")

_pymod_termination_hook = get_pymod_termination_hook()


#TODO: LATE: The following list of events/event providers could be useful. A new class would be needed that provides an interface using Talon's `dispatch.pyi`. `MessageLoop` would need an invisible window to receive messages that's created if the handler `on_window_message` is passed (not a message-only window, because these don't receive broadcast messages).
#      - WTSRegisterSessionNotification()
#      - [Power management functions](https://learn.microsoft.com/en-us/windows/win32/power/power-management-functions)
#        - E.g., switching screen on and off: `RegisterPowerSettingNotification()` (windowless variant: `PowerSettingRegisterNotification()`). (The docs' remarks for `PowerSettingRegisterNotification()` say something important that isn't stated for `RegisterPowerSettingNotification()`, although the latter *seems* to behave the same.)
#      - `RegisterWindowMessageW("TaskbarCreated")` (after `explorer.exe` restart) and `RegisterWindowMessageW("TaskbarButtonCreated")`
#      - RegisterShellHookWindow()
#      - WM_QUERYENDSESSION, WM_ENDSESSION, WM_POWERBROADCAST, WM_SETTINGCHANGE, WM_DISPLAYCHANGE, (WM_DEVMODECHANGE), WM_DEVICECHANGE, WM_SYSCOLORCHANGE, WM_THEMECHANGED
#      - AddClipboardFormatListener()
#      - `SetWinEventHook()` (only select events)
#      - SHChangeNotifyRegister()
class MessageLoop:
    """Maintains a separate thread with a Win32 message loop.
    
    You can post messages in its queue and will receive them again in a callback running in the separate thread. The message loop also handles messages like those registered with Win32 hook functions by calling your callbacks specified there.

    This class is thread-safe.
    """

    def __init__(
        self,
        instance_uuid4: UUID,
        *,
        on_thread_created: Callable[[], None] | None = None,
        on_unique_message: Callable[[int, int], None] | None = None,
        on_thread_exit: Callable[[], None] | None = None,
    ) -> None:
        """
        - When you return from `on_thread_created()`, the constructor will still not have returned.
        - The callbacks are called in the separate thread.
        - `on_unique_message()` receives the arguments posted with `post_unique()`.
        - The instance takes care of calling `on_thread_exit()` on module and instance finalization. You must use `functools.partial()` for it and must not directly or indirectly bind it to your instance that holds this `MessageLoop` instance to avoid a reference cycle that hinders garbage collection and keeps the separate thread alive when missing a call to `quit()`. Think of it as similar to `weakref.finalize()`. The callback reference is released after thread exit.
        - The UUIDv4 is used in messages.
        """

        # Pre-thread preparation.
        self.__label = f'`{MessageLoop.__name__}` with UUID "{instance_uuid4}"'

        self.__weak_downstream_on_thread_created = to_weak_callback(on_thread_created) if on_thread_created is not None else None
        self.__weak_downstream_on_unique_message = to_weak_callback(on_unique_message) if on_unique_message is not None else None
        if not isinstance(on_thread_exit, functools.partial):
            raise TypeError("Expected type `functools.partial` for `on_thread_exit`.")
        self.__downstream_on_thread_exit = on_thread_exit

        self.__lock = Lock()

        self.__unique_message_id: int = user32.RegisterWindowMessageW("Talon.WindowsToolkit.MessageLoop.{4dbac389-d878-44b1-b358-17fa6cc04375}")
        if not self.__unique_message_id:
            raise ctypes.WinError(kernel32.GetLastError())
        #i Combined with thread ID. Not `WM_USER+x`/`WM_APP+x`, because we don't know what Talon or user code might post to threads.

        self.__thread_ready = Event()
        self.__must_quit_asap_event = Event()

        # Start thread.
        self.__thread = Thread(
            target=MessageLoop.__thread_main,
            args=(weakref.ref(self),),
            #i A weak reference avoids a reference cycle, making garbage collection apart from module finalization possible.
            name=f"MessageLoop-{instance_uuid4}",
            daemon=True,
            #i The `daemon` flag will have to suffice to quit the thread when terminating Talon, since Talon v0.4.0 doesn't seem to provide a working event for that case.
        )
        self.__thread.start()

        _pymod_termination_hook.on_module_finalize(self.__on_pymod_finalize)
        self.__thread_finalizer = weakref.finalize(self, MessageLoop.__finalize_thread, self.__thread.native_id, self.__must_quit_asap_event, asap=True)

        # Ensure initialization in thread is done before allowing to work with the instance.
        timed_out = not self.__thread_ready.wait(timeout=3)
        if timed_out:
            raise TimeoutError(f"Thread of {self.__label} didn't become ready before timeout.")

    @staticmethod
    def __thread_main(weak_self: ReferenceType["MessageLoop"]) -> None:
        # Initialize.
        strong_self = weak_self()
        assert type(strong_self) is MessageLoop

        msg: Any = wapi.new("MSG *")
        user32.PeekMessageW(msg, wapi.NULL, 0, 0, user32.PM_NOREMOVE)
        #i "The functions that are guaranteed to create a message queue are `Peek­Message`, `Get­Message`, and `Create­Window`." (<https://devblogs.microsoft.com/oldnewthing/20241009-00/?p=110354>) But `GetMessageW()` blocks.

        call_weak(strong_self.__weak_downstream_on_thread_created)

        # # Keep available, even if `strong_self` disappears. (The contructor forbade it to be a bound method.)
        downstream_on_thread_exit = strong_self.__downstream_on_thread_exit

        #
        with _pymod_termination_hook.globals_teardown_deferrer:
            # Cause constructor to return.
            strong_self.__thread_ready.set()
            del strong_self

            # Run loop.
            def print_exception(strong_self: MessageLoop) -> None:
                print(
                    f"ERROR: Unhandled exception in thread of {strong_self.__label}:\n"
                    + textwrap.indent(traceback.format_exc().rstrip(), "  ")
                )

            while True:
                result: int = user32.GetMessageW(msg, wapi.NULL, 0, 0)
                if result == -1:
                    raise ctypes.WinError(kernel32.GetLastError())
                #i `GetMessageW()` internally calls registered callbacks like those from hooks.

                strong_self = weak_self()
                if strong_self is None:
                    break

                if strong_self.__must_quit_asap_event.is_set():
                    break

                try:
                    if msg.hwnd == wapi.NULL:  # Thread message.
                        match msg.message:
                            case strong_self.__unique_message_id:
                                call_weak(
                                    strong_self.__weak_downstream_on_unique_message,
                                    msg.wParam,
                                    msg.lParam,
                                )
                            case user32.WM_QUIT:
                                break
                except BaseException:
                    print_exception(strong_self)

                del strong_self

            # Run exit callback.
            strong_self = weak_self()

            try:
                if downstream_on_thread_exit is not None:
                    downstream_on_thread_exit()
            except BaseException:
                if strong_self is not None:
                    print_exception(strong_self)
                else:
                    raise
            finally:
                if strong_self is not None:
                    # Release any referenced objects.
                    strong_self.__downstream_on_thread_exit = None

            del strong_self

    def post_unique(self, arg_1: int = 0, arg_2: int = 0) -> None:
        """Posts a unique message with the specified arguments to the message queue of the separate thread, after which your `on_unique_message()` callback will be called.

        The first argument can be an unsigned and the second argument a signed pointer-sized value. Values out of range raise an `OverflowError`.
        """

        with self.__lock:
            if self.__thread is None:
                raise RuntimeError(f"{self.__label} already quit.")

            success = bool(user32.PostThreadMessageW(
                self.__thread.native_id,
                self.__unique_message_id,
                arg_1,
                arg_2,
                #i CFFI may raise `OverflowError` for `WPARAM` or `LPARAM`.
            ))
            if not success:
                raise ctypes.WinError(kernel32.GetLastError())

    def quit(self, wait: bool = True, asap: bool = False) -> None:
    #i Modeled after `Executor.shutdown()`.
        """Quits the separate thread with the actual message loop, so that other methods raise an exception when called.

        If `wait` is `True`, the method only returns after the message loop thread exited.

        `asap` specifies whether the message loop should quit as soon as possible, i.e., immediately after fetching the next message without processing it, meaning all pending queue entries will be ignored.
        """

        with self.__lock:
            if self.__thread is None:
                return

            MessageLoop.__finalize_thread(cast(int, self.__thread.native_id), self.__must_quit_asap_event, asap)
            self.__thread_finalizer.detach()
            #i Since the finalizer acts after garbage collection of `self` and this method references `self`, there's no race condition with the finalizer.

            thread = self.__thread
            self.__thread = None  # Signal to other methods.

        if wait:
            thread.join()

    def __on_pymod_finalize(self) -> None:
        self.quit(wait=False, asap=True)

    @staticmethod
    def __finalize_thread(native_id: int, asap_event: Event, asap: bool) -> None:
        if asap:
            asap_event.set()

        success = bool(user32.PostThreadMessageW(native_id, user32.WM_QUIT, 0, 0))
        if not success:
            last_error: int = kernel32.GetLastError()
            if last_error == winerror.ERROR_INVALID_THREAD_ID:  # Already terminated.
                pass
            else:
                raise ctypes.WinError(last_error)


class MessageLoopExecutor(Executor):
    """Executes calls in a separate thread with a Win32 message loop.

    Callbacks registered with `concurrent.futures.Future.add_done_callback()` may run in the separate message loop thread or in any of those that you called this class's API from.

    When a reload resilience mechanism couldn't retrieve your instance for you to shut it down gracefully, indefinitely waiting for one of the produced `Future`s blocks Talon until the instance is garbage-collected.

    This class is thread-safe.
    """

    OnThreadExitCallback: TypeAlias = Callable[[], None]
    __PendingJobsDeque: TypeAlias = deque[tuple[Callable[[], object], Future[object]]]

    def __init__(
        self,
        instance_uuid4: UUID,
        *,
        on_thread_created: Callable[[], None] | None = None,
        on_thread_exit: OnThreadExitCallback | None = None,
    ) -> None:
        """See `MessageLoop` for information about the arguments."""

        self.__label = f'`{MessageLoopExecutor.__name__}` with UUID "{instance_uuid4}"'
        self.__lock = Lock()
        self.__pending_jobs: MessageLoopExecutor.__PendingJobsDeque = deque()
        """FIFO queue."""

        self.__is_shut_down_event = Event()
        if not isinstance(on_thread_exit, functools.partial):
            raise TypeError("Expected type `functools.partial` for `on_thread_exit`.")

        self.__message_loop = MessageLoop(
            instance_uuid4,
            on_thread_created=on_thread_created,
            on_unique_message=self.__on_unique_message,
            on_thread_exit=functools.partial(
                MessageLoopExecutor.__on_thread_exit,
                on_thread_exit,
                self.__lock,
                self.__is_shut_down_event,
                self.__pending_jobs,
                self.__label,
            ),
        )
        #i `MessageLoop` takes care of calling the thread-exit callback on finalization.

    def submit(
        self,
        func: Callable[P, T],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Future[T]:
        with self.__lock:
            if self.__is_shut_down_event.is_set():
                raise RuntimeError(f"{self.__label} already shut down.")

            future = Future()
            self.__pending_jobs.append((functools.partial(func, *args, **kwargs), future))

            try:
                self.__message_loop.post_unique()
            except BaseException:
                # Rollback.
                self.__pending_jobs.pop()
                raise

        return future

    def invoke(
        self,
        func: Callable[P, T],
        /,
        *args: P.args,
        timeout: float | None = None,  # pyright: ignore[reportGeneralTypeIssues]
        **kwargs: P.kwargs,
    ) -> T:
        """Convenience function that synchronously calls a function in the message loop thread by calling `submit()` and waiting for the `Future` to complete."""

        return self.submit(func, *args, **kwargs).result(timeout)

    def __on_unique_message(self, arg_1: int, arg_2: int) -> None:
        with self.__lock:
            try:
                func, future = self.__pending_jobs.popleft()
            except IndexError:
                return

        running = future.set_running_or_notify_cancel()
        if running:
            try:
                future.set_result(func())
            except BaseException as e:
                future.set_exception(e)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """See Python docs for `Executor.shutdown()`. Will cause the message loop thread to exit."""

        futures_to_cancel = []

        with self.__lock:
            if self.__is_shut_down_event.is_set():
                return

            self.__is_shut_down_event.set()

            if cancel_futures:
                while self.__pending_jobs:
                    _, future = self.__pending_jobs.popleft()
                    futures_to_cancel.append(future)

        for future in reversed(futures_to_cancel):
            future.cancel()
            #i Can run any third-party code registered with `Future.add_done_callback()`, which is why it shouldn't be done while holding the lock.

        self.__message_loop.quit(wait, asap=cancel_futures)

    @staticmethod
    def __on_thread_exit(
        downstream_on_thread_exit: OnThreadExitCallback | None,
        lock: Lock,
        is_shut_down_event: Event,
        pending_jobs: __PendingJobsDeque,
        label: str,
    ):
        try:
            if downstream_on_thread_exit is not None:
                downstream_on_thread_exit()
            #i Calling this first ensures removing hooks and such will unburden the message queue from incoming events before possible `Future` done-callbacks run.
        finally:
            with lock:
                is_shut_down_event.set()

                # Prevent waiting for `Future`s that can never complete.
                message = None
                while pending_jobs:
                    _, future = pending_jobs.pop()  # From back to front.

                    if message is None:
                        message = f"Thread of {label} exited."

                    try:
                        future.set_exception(BrokenExecutor(message))
                        #i May run third-party code. See `shutdown()`.
                    except InvalidStateError:  # `Future` already done.
                        pass
