from threading import Lock
from typing import Optional

from talon import actions, cron, Module, ui
from talon.cron import Job

from .constants import WinEvent, ObjectID
from .tracker import Subfilter, WinEventTracker

_mod = Module()

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
            user.wait_for_focus()
        """

        global _tracker, _tracker_cleanup_job

        with _lock:
            cron.cancel(_tracker_cleanup_job)
            _tracker_cleanup_job = None

            if _tracker:
                try:
                    _tracker.__exit__()
                finally:
                    _tracker = None

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

            _tracker_cleanup_job = cron.after("5s", _on_tracker_cleanup_job)  # Timeout reflected in docstring.
            _tracker.__enter__()

    #TODO: To be usable everywhere, the signature needs a mandatory fallback duration as first parameter (time spec string?). Then, default actions can be implemented where `track_focus()` is a no-op and `wait_for_focus()` just sleeps for the default duration.
    def wait_for_focus(timeout: float = 1.0):
        """Waits until a UI element seems to have acquired focus. You must have called `user.track_focus()` first.

        Raises an exception on timeout. There's an additional non-configurable 5-second timeout after which tracking is automatically aborted.
        """

        global _tracker, _tracker_cleanup_job

        with _lock:
            if not _tracker:
                raise RuntimeError("Can't wait for focus without ongoing tracking.")

            cron.cancel(_tracker_cleanup_job)
            _tracker_cleanup_job = None

            tracker = _tracker
            _tracker = None

        try:
            tracker.require(
                (WinEvent.OBJECT_FOCUS, WinEvent.OBJECT_LOCATIONCHANGE),
                timeout=timeout,
            )  # May raise `TimeoutError`.
        finally:
            tracker.__exit__()

    def private_test_wait_for_focus():
        """Test for the `user.wait_for_focus()` action."""

        import time

        print("Tracking focus.")
        actions.user.track_focus()

        #i You can optionally insert focus-acquiring code here.

        start_time = time.perf_counter()
        actions.user.wait_for_focus(timeout=3)
        waiting_duration = time.perf_counter() - start_time

        print(f"Successfully waited for focus. Duration: {waiting_duration * 1000:.3f} ms.")

    def private_test_win_event_tracker():
        """Test for the `WinEventTracker` class."""

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


def _on_tracker_cleanup_job():
    global _tracker, _tracker_cleanup_job

    with _lock:
        _tracker_cleanup_job = None

        if _tracker:
            try:
                _tracker.__exit__()
            finally:
                _tracker = None
