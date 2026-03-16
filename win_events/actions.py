from collections.abc import Sequence
from threading import Lock
from typing import cast, Union

from talon import actions, Context, cron, Module, settings, ui
from talon.cron import Job

from .constants import WinEvent, ObjectID
from .tracker import Subfilter, WinEventTracker

_mod = Module()

_mod.tag(
    "wtk_tracking__active",
    desc="Activates Windows Toolkit's win event tracking for a better alternative to fixed waiting durations in the `user.wait_for_…()` actions.",
)
_mod.setting(
    "wtk_tracking__window_activation_timeout",
    type=float,
    default=2.5,
    desc="Seconds after which the `user.wait_for_window_activation()` actions raise a `TimeoutError`. Note that there's an additional non-configurable few-second timeout after which tracking is automatically aborted if a call to `user.track_window_activation()` isn't matched by a call to `user.wait_for_window_activation()`.",
)
_mod.setting(
    "wtk_tracking__focus_timeout",
    type=float,
    default=0.9,
    desc="Same as `user.wtk_tracking__window_activation_timeout`, but for `user.…_focus()`.",
)

_ctx = Context()
_ctx.matches = """
os: windows
tag: user.wtk_tracking__active
"""  # pyright: ignore[reportAttributeAccessIssue]

_lock = Lock()
_tracker: WinEventTracker | None = None
_tracker_cleanup_job: Job | None = None


#TODO: WITH COMMUNITY PEOPLE: Have default actions in `community`, allowing users to install their tracker of choice that works for their OS? This would be similar to the default support of VS Code plus better support when installing the briding VS Code extension.
@_mod.action_class
class _Actions:
    @staticmethod
    def track_window_activation() -> None:
        """Starts to track events about window activation.

        Usage:

            user.track_window_activation()
            user.your_activation_action()
            user.wait_for_window_activation("300ms")
        """

        if True:
            pass

    @staticmethod
    def wait_for_window_activation(fixed_fallback_duration: Union[float, str]) -> None:
        """Waits until a window seems to have been activated. You must have called `user.track_window_activation()` first."""

        actions.sleep(fixed_fallback_duration)

    @staticmethod
    def track_focus() -> None:
        """Starts to track events from the active window that typically occur after a UI element focus change in most apps. Because this includes caret location change events, the caret must not still be moving from the previous action.

        Usage:

            user.track_focus()
            user.your_focus_action()
            user.wait_for_focus("100ms")
        """

        if True:
            pass

    @staticmethod
    def wait_for_focus(fixed_fallback_duration: Union[float, str]) -> None:
        """Waits until a UI element seems to have acquired focus. You must have called `user.track_focus()` first."""

        actions.sleep(fixed_fallback_duration)


@_ctx.action_class("user")
class _UserActions:
    @staticmethod
    def track_window_activation() -> None:
        global _tracker

        with _lock:
            _clean_up_tracker()

            _tracker = WinEventTracker(Subfilter(WinEvent.SYSTEM_FOREGROUND))
            _start_tracker()

    @staticmethod
    def wait_for_window_activation(fixed_fallback_duration: Union[float, str]) -> None:
        _wait_for_winevents(
            WinEvent.SYSTEM_FOREGROUND,
            timeout=cast(float, settings.get("user.wtk_tracking__window_activation_timeout")),
        )

    @staticmethod
    def track_focus() -> None:
        global _tracker

        with _lock:
            _clean_up_tracker()

            _tracker = WinEventTracker(
                Subfilter(
                    # E.g., Windows save dialog (Windows 11 Home 25H2) or browsers (March 2026).
                    WinEvent.OBJECT_FOCUS,
                    object_id=ObjectID.CLIENT,
                    #i Target can be object itself or not. If it is, no child seems to mean the focused element.
                ),
                Subfilter(
                    # E.g., Windows Terminal (Windows 11 Home 25H2).
                    WinEvent.OBJECT_FOCUS,
                    object_id_is_custom=True,
                ),
                #i Unfortunately, `OBJECT_FOCUS` events also happen when just hovering over menu *bar* items with the mouse in at least the UI frameworks Chrome, Gecko and Qt. At least in Gecko, this also applies when the window isn't even active. Going by the behavior of Win32 apps, the correct way would be to only send the event on mouse-enter when the arrow keys would also change the selection, like after pressing Alt. (Qt also goes haywire when hovering over menu items [not of menu *bars*], sending the event on every mouse-move.) If a workaround is needed, `Role.MENUITEM` could be excluded; although this would go a little too far.
                Subfilter(
                    # E.g., VS Code v1.109.5.
                    WinEvent.OBJECT_LOCATIONCHANGE,
                    object_id=ObjectID.CARET,
                ),
                # Subfilter(
                #     # Undocumented event not fully reliably correlated with focus changes in various apps. AI guessed it's associated with the UI Automation API.
                #     events=0x7FFF_FF30,  # Needs support of accepting `int`s instead of just `WinEvent`s.
                # ),
                inclusive_ancestor_hwnd=ui.active_window().id,
            )
            _start_tracker()

    @staticmethod
    def wait_for_focus(fixed_fallback_duration: Union[float, str]) -> None:
        _wait_for_winevents(
            (WinEvent.OBJECT_FOCUS, WinEvent.OBJECT_LOCATIONCHANGE),
            timeout=cast(float, settings.get("user.wtk_tracking__focus_timeout")),
        )


@_mod.action_class
class _TestActions:
    @staticmethod
    def private_wtk_test_win_event_tracker() -> None:
        """Simple test for the `WinEventTracker` class."""

        caret_tracker = WinEventTracker(
            Subfilter(
                WinEvent.OBJECT_LOCATIONCHANGE,
                object_id=ObjectID.CARET,
            ),
            Subfilter(WinEvent.OBJECT_TEXTSELECTIONCHANGED),  # UWP apps.
            timeout=3,
        )
        generic_tracker = WinEventTracker(Subfilter(
            slice(WinEvent.MIN, WinEvent.MAX),
        ))

        with caret_tracker, generic_tracker:
            print("Waiting for caret standstill.")
            caret_tracker.require_silence(1)

            print("Waiting for caret movement.")
            caret_tracker.reset_wait_start()
            caret_tracker.require(WinEvent.OBJECT_LOCATIONCHANGE)

            print("Waiting for focus event.")
            generic_tracker.reset_wait_start()
            generic_tracker.require(WinEvent.OBJECT_FOCUS, timeout=6)

            print("Done waiting.")

    @staticmethod
    def private_wtk_test_wait_for_focus() -> None:
        """Test for the `user.wait_for_focus()` action."""

        import time

        print("Tracking focus.")
        actions.user.track_focus()

        #i You can optionally insert focus-acquiring code here.

        start_time = time.perf_counter()
        actions.user.wait_for_focus("100ms")
        waiting_duration = time.perf_counter() - start_time

        print(f"Successfully waited for focus. Duration: {waiting_duration * 1000:.3f} ms.")


def _start_tracker() -> None:
    """The caller is responsible for locking."""

    global _tracker_cleanup_job, _tracker

    _tracker_cleanup_job = cron.after("5s", _on_tracker_cleanup_job)

    assert _tracker is not None
    _tracker.__enter__()

def _wait_for_winevents(win_events: WinEvent | Sequence[WinEvent], timeout: float) -> None:
    global _tracker

    with _lock:
        if _tracker is None:
            raise RuntimeError("Can't wait without ongoing tracking.")

        active_tracker = _tracker
        _clean_up_tracker(False)

    try:
        active_tracker.require(
            win_events,
            timeout,
        )  # May raise `TimeoutError`.
    finally:
        active_tracker.__exit__()

def _clean_up_tracker(may_exit_context: bool = True) -> None:
    """The caller is responsible for locking."""

    global _tracker_cleanup_job, _tracker

    cron.cancel(_tracker_cleanup_job)
    _tracker_cleanup_job = None

    if _tracker is not None:
        try:
            if may_exit_context:
                _tracker.__exit__()
        finally:
            _tracker = None

def _on_tracker_cleanup_job() -> None:
    global _tracker, _tracker_cleanup_job

    with _lock:
        had_tracker = _tracker is not None
        _tracker_cleanup_job = None
        _clean_up_tracker()

    if had_tracker:
        raise RuntimeError("Tracking aborted because waiting was never initiated.")
