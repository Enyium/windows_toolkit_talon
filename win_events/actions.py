from threading import Lock
from typing import Optional, Sequence, Union

from talon import actions, Context, cron, Module, settings, ui
from talon.cron import Job

from .constants import WinEvent, ObjectID
from .tracker import Subfilter, WinEventTracker

_mod = Module()
_mod.setting(
    "si_tracking__waiting_timeout",
    type=float,
    default=1.0,
    desc="Seconds after which the `user.wait_for_...()` actions raise a `TimeoutError`. Note that there's an additional non-configurable few-second timeout after which tracking is automatically aborted if a call to `user.track_...()` isn't matched by a call to `user.wait_for_...()`.",
)

_ctx = Context()
_ctx.matches = """
os: windows
"""

_lock = Lock()
_tracker: Optional[WinEventTracker] = None
_tracker_cleanup_job: Optional[Job] = None


#TODO: When quickly switching to another window (not when letting the user control the switcher window), it may be beneficial to wait for `WinEvent.SYSTEM_SWITCHEND`. But maybe directly in .py code.
@_mod.action_class
class _Actions:
    def track_focus():
        """Starts to track events from the active window that typically occur after a UI element focus change in most apps. Because this includes caret location change events, the caret must not still be moving from the previous action.

        Usage:

            user.track_focus()
            user.your_focus_action()
            user.wait_for_focus(100ms)
        """

    def wait_for_focus(fixed_fallback_duration: Union[float, str]):
        """Waits until a UI element seems to have acquired focus. You must have called `user.track_focus()` first."""

        actions.sleep(fixed_fallback_duration)


@_ctx.action_class("user")
class _UserActions:
    def track_focus():
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

    def wait_for_focus(fixed_fallback_duration: Union[float, str]):
        _wait_for((WinEvent.OBJECT_FOCUS, WinEvent.OBJECT_LOCATIONCHANGE))


@_mod.action_class
class _TestActions:
    def private_si_test_win_event_tracker():
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

    def private_si_test_wait_for_focus():
        """Test for the `user.wait_for_focus()` action."""

        import time

        print("Tracking focus.")
        actions.user.track_focus()

        #i You can optionally insert focus-acquiring code here.

        start_time = time.perf_counter()
        actions.user.wait_for_focus("100ms")
        waiting_duration = time.perf_counter() - start_time

        print(f"Successfully waited for focus. Duration: {waiting_duration * 1000:.3f} ms.")


def _start_tracker():
    """The caller is responsible for locking."""

    global _tracker_cleanup_job, _tracker
    _tracker_cleanup_job = cron.after("5s", _on_tracker_cleanup_job)
    _tracker.__enter__()

def _wait_for(win_events: Union[WinEvent, Sequence[WinEvent]]):
    global _tracker

    with _lock:
        if not _tracker:
            raise RuntimeError("Can't wait without ongoing tracking.")

        active_tracker = _tracker
        _clean_up_tracker(False)

    try:
        active_tracker.require(
            win_events,
            timeout=settings.get("user.si_tracking__waiting_timeout"),
        )  # May raise `TimeoutError`.
    finally:
        active_tracker.__exit__()

def _clean_up_tracker(may_exit_context: bool = True):
    """The caller is responsible for locking."""

    global _tracker_cleanup_job, _tracker

    cron.cancel(_tracker_cleanup_job)
    _tracker_cleanup_job = None

    if _tracker:
        try:
            if may_exit_context:
                _tracker.__exit__()
        finally:
            _tracker = None

def _on_tracker_cleanup_job():
    global _tracker, _tracker_cleanup_job

    with _lock:
        had_tracker = _tracker is not None
        _tracker_cleanup_job = None
        _clean_up_tracker()

    if had_tracker:
        raise RuntimeError("Tracking aborted because waiting was never initiated.")
