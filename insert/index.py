"""
Reimplements Talon's `insert()` function and provides Talon settings that allow it to be configured.
"""

import ctypes
import time
from contextlib import nullcontext
from typing import Any, cast

import win32api
import win32con
import winerror
from talon import Context, Module, settings

from ..lib.winapi import CData, kernel32, user32, wapi
from ..win_events.constants import ObjectID, Role, WinEvent
from ..win_events.tracker import Subfilter, WinEventTracker

_mod = Module()

_mod.tag(
    "wtk_insert__active",
    desc="Activates Windows Toolkit's `insert()` override.",
)
_mod.setting(
    "wtk_insert__yield_time",
    type=bool,
    default=False,
    desc="Whether `insert()` waits before every character until the target window's message pump continues. This can be necessary to prevent the target window from mixing up characters, especially if significant work like spell checking is done after every new character.",
)
_mod.setting(
    "wtk_insert__caret_still_ms",
    type=float,
    default=40,
    desc="After sending the events for a chunk of text, incl. the last, these are the number of milliseconds the caret (text input cursor) position must not change until the target window is regarded as ready to receive further input. Note that, because of visual lag, the standstill may appear to be much shorter than it actually is eventwise; the criterion for determination of the value's magnitude is correct input. There are UI frameworks that don't report carets in a manner recognizable by Windows Toolkit, in which case the waiting duration remains fixed and won't be extended. A value of 0 turns off all waiting for caret standstill. The concrete situations of waiting are controlled by some of the boolean settings.",
)
_mod.setting(
    "wtk_insert__caret_still_before_supp_char",
    type=bool,
    default=False,
    desc="Whether the setting `user.wtk_insert__caret_still_ms` applies when transitioning from a Unicode BMP character to a supplementary character. This can be necessary to prevent the characters from being mixed up.",
)
_mod.setting(
    "wtk_insert__caret_still_before_tab",
    type=bool,
    default=False,
    desc=r"""Whether the setting `user.wtk_insert__caret_still_ms` applies before `"\t"`.""",
)
_mod.setting(
    "wtk_insert__caret_still_before_enter",
    type=bool,
    default=False,
    desc=r"""Whether the setting `user.wtk_insert__caret_still_ms` applies before `"\n"`.""",
)
_mod.setting(
    "wtk_insert__caret_still_before_backspace",
    type=bool,
    default=True,
    desc=r"""Whether the setting `user.wtk_insert__caret_still_ms` applies before `"\b"`. This may make a preceding character like space reliable in dismissing suggestion overlays.""",
)
_mod.setting(
    "wtk_insert__caret_still_before_esc",
    type=bool,
    default=True,
    desc=r"""Whether the setting `user.wtk_insert__caret_still_ms` applies before `"\N{ESC}"`. This may make Esc reliable in dismissing suggestion overlays.""",
)
_mod.setting(
    "wtk_insert__caret_still_before_last_char",
    type=bool,
    default=False,
    desc="Whether the setting `user.wtk_insert__caret_still_ms` applies before the last character of the text insertion. This is useful when the fast insertion of previous text leaves the text box in an incorrect state (like, e.g., with wavy red underlining to incorrectly indicate an error) that is rectified by calm insertion of the final character. The setting is only applied for text with 2 or more characters.",
)
_mod.setting(
    "wtk_insert__caret_still_at_end",
    type=bool,
    default=True,
    desc="Whether the setting `user.wtk_insert__caret_still_ms` applies at the end of the text insertion. This is useful to ensure a settled state for follow-up voice commands. When `user.wtk_insert__caret_still_before_last_char` is also set, `insert()` still waits for caret standstill, but this waiting duration is shortened.",
)

_ctx = Context()
_ctx.matches = """
tag: user.wtk_insert__active
"""  # pyright: ignore[reportAttributeAccessIssue]

_INSERTION_TIMEOUT = 30
"""Seconds until a single insertion session is aborted."""

_MODIFIER_KEYS = (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_MENU, win32con.VK_LWIN, win32con.VK_RWIN)
_MOUSE_KEYS = (win32con.VK_LBUTTON, win32con.VK_RBUTTON, win32con.VK_MBUTTON, win32con.VK_XBUTTON1, win32con.VK_XBUTTON2)


@_ctx.action_class("main")
class _MainActions:
    @staticmethod
    def insert(text: str):
        """A reimplementation and replacement of Talon's original action that is much faster, doesn't have issues with dead keys, and is more resilient against interference.

        Characters are sent as themselves rather than their respective key presses, which are keyboard-layout-dependent. This also comes with independence from the caps lock state. In some apps/web apps, inserting characters with this override is not a working alternative to pressing them using Talon's `key()` action, which Talon's original `insert()` seems to use.

        Real key events are only simulated for the following keys:

        - Tab (`\t`) - As with Talon's `insert()`, you must be careful not to accidentally accept editor suggestions. For most code use cases, only ever inserting `\t` at the start of a line or after other whitespace should suffice. If you work with TSV (tab-separated values) or something like that, you may need to turn off automatically appearing suggestion overlays completely.
        - Enter (`\n`) - As for Talon's `insert()`, you should turn off accepting suggestions with Enter altogether in every app, because characters triggering these overlays at the end of lines are basically unavoidable. See also Windows Toolkit's readme.
        - Backspace (`\b`) - Finalizing the current word with, e.g., a space character and deleting it again can dismiss a suggestion overlay, but race conditions may cause problems in certain apps. The setting `user.wtk_insert__caret_still_before_backspace` may help.
        - Esc (`\N{ESC}`, `\x1b`) - Can dismiss a suggestion overlay to prevent confirming it, but race conditions may cause problems in certain apps. The setting `user.wtk_insert__caret_still_before_esc` may help. In general, success depends too much on app settings and IDE state (find box incl. its highlights, etc.).

        You can use the following tags and settings to configure the behavior of the action:

        - `user.wtk_insert__active`
        - `user.wtk_insert__yield_time`
        - `user.wtk_insert__caret_still_ms`
        - `user.wtk_insert__caret_still_before_supp_char`
        - `user.wtk_insert__caret_still_before_tab`
        - `user.wtk_insert__caret_still_before_enter`
        - `user.wtk_insert__caret_still_before_backspace`
        - `user.wtk_insert__caret_still_before_esc`
        - `user.wtk_insert__caret_still_before_last_char`
        - `user.wtk_insert__caret_still_at_end`
        """

        _InsertSession()(text)


class _InsertSession:
    __VKS_BY_SELECT_ASCII_CODES = {
        # Keyboard-layout-invariant virtual-key codes that don't work with `SendInput()`'s `KEYEVENTF_UNICODE` flag.
        0x08: win32con.VK_BACK,
        0x09: win32con.VK_TAB,
        0x0A: win32con.VK_RETURN,
        0x1B: win32con.VK_ESCAPE,
    }

    def __init__(self) -> None:
        self.__deadline = None

        self.__must_yield_time = cast(bool, settings.get("user.wtk_insert__yield_time"))
        self.__caret_still_duration = max(0, cast(float, settings.get("user.wtk_insert__caret_still_ms")) / 1000)
        self.__must_wait_before_supp_char = cast(bool, settings.get("user.wtk_insert__caret_still_before_supp_char"))
        self.__must_wait_before_tab = cast(bool, settings.get("user.wtk_insert__caret_still_before_tab"))
        self.__must_wait_before_enter = cast(bool, settings.get("user.wtk_insert__caret_still_before_enter"))
        self.__must_wait_before_backspace = cast(bool, settings.get("user.wtk_insert__caret_still_before_backspace"))
        self.__must_wait_before_esc = cast(bool, settings.get("user.wtk_insert__caret_still_before_esc"))
        self.__must_wait_before_last_char = cast(bool, settings.get("user.wtk_insert__caret_still_before_last_char"))
        self.__must_wait_at_end = cast(bool, settings.get("user.wtk_insert__caret_still_at_end"))

        self.__events: Any = None
        self.__num_events: int = 0

        self.__gui_thread_info: Any = wapi.new("GUITHREADINFO *", {"cbSize": wapi.sizeof("GUITHREADINFO")})
        self.__insertion_toplevel_hwnd: CData | None = None
        self.__insertion_hwnd: CData | None = None
        self.__interference_tracker: WinEventTracker | None = None

    def __call__(self, text: str) -> None:
        if not text:
            return
        #i TalonScript code sometimes contains `insert(capture or "")`.

        self.__deadline = time.perf_counter() + _INSERTION_TIMEOUT

        # Establish windows.
        self.__fill_gui_thread_info(may_retry=True)

        self.__insertion_toplevel_hwnd = self.__gui_thread_info.hwndActive
        assert self.__insertion_toplevel_hwnd
        self.__insertion_hwnd = self.__get_insertion_hwnd(self.__gui_thread_info)
        #i Keyboard input may instead effectively go to yet a different menu window, owned by the top-level window, even though it's not reported as active or focused. It can be a Win32 menu window or from various UI frameworks.

        #TODO: WITH TALON AUTHOR: The disagreement happens regularly, like when saying "sway word bar" (with Notepad as 2nd window and "sway" waiting for `WinEvent.SYSTEM_FOREGROUND`), even though `GetForegroundWindow()` and `GetGUIThreadInfo(0, …)` return the correct target window (Notepad). Perhaps, `ui.active_window()` should call `GetForegroundWindow()` every time (and possibly only build or retrieve a `Window` object on HWND changes).
        # active_window = ui.active_window()
        # if wapi.cast("HWND", active_window.id) != self.__insertion_toplevel_hwnd:
        #     raise RuntimeError(f"Talon and utilized WinAPI disagree about active window during text insertion. Talon: `{active_window}` (ID 0x{active_window.id:X}). `GUITHREADINFO.hwndActive`: {self.__insertion_toplevel_hwnd}.")

        # Limit insertion if Win32 menu active.
        if (
            self.__gui_thread_info.flags
            & (
                win32con.GUI_SYSTEMMENUMODE
                | win32con.GUI_INMENUMODE
                | win32con.GUI_POPUPMENUMODE
            )
            #i This check only covers traditional Win32 menus incl. windows' system menus. The universal way would be to check whether `talon.windows.ax.get_focused_element().control_type` is `"MenuBar"` or `"MenuItem"`. But unfortunately, this API is very slow and would introduce a delay of up to about 83 ms, according to the author's measurements.
            and len(text) > 1
        ):
            raise RuntimeError("Received more than one character to insert while menu is active. Text insertion aborted.")

        # Set up win event trackers.
        self.__interference_tracker = WinEventTracker(
            Subfilter(
                slice(WinEvent.SYSTEM_MENUSTART, WinEvent.SYSTEM_MENUPOPUPEND),
                #i Can be somewhat out of order (seen in Firefox v149 with persistent menu bar). The Microsoft docs also talk about this to some degree.
            ),
        )

        must_wait_before_last_char = self.__must_wait_before_last_char and len(text) >= 2

        caret_tracker = (
            WinEventTracker(
                Subfilter(
                    # Most apps.
                    WinEvent.OBJECT_LOCATIONCHANGE,
                    #i In Windows Terminal, both `OBJECT_LOCATIONCHANGE` and `CONSOLE_CARET` didn't work. In `conhost.exe`, they did. (Windows 11 Home 24H2)
                    object_id=ObjectID.CARET,
                ),
                Subfilter(
                    # UWP and similar Microsoft apps.
                    WinEvent.OBJECT_TEXTSELECTIONCHANGED,
                ),
                Subfilter(
                    # At least Windows Terminal (Windows 11 Home 25H2) when `OBJECT_TEXTSELECTIONCHANGED` isn't emitted because selected text is overwritten.
                    WinEvent.OBJECT_VALUECHANGE,
                    role=Role.STATICTEXT,  # Yes, static.
                ),
                inclusive_ancestor_hwnd=int(wapi.cast("uintptr_t", self.__insertion_toplevel_hwnd)),
                timeout=_INSERTION_TIMEOUT,
                #i Note that apps' UIs and their signaling of win events may not be in sync. E.g., in VS Code v1.109.5, the sent text may already be presented while `OBJECT_LOCATIONCHANGE` events continue to arrive for a moment, making caret waits appear longer. In Notepad of Windows 11 Home 25H2, the arrival of win events may already have ceased while text still continues to appear, visually smoothing out caret waits that were actually longer than they appeared to be. (Printing on win event reception is better than using AccEvent to understand this effect.)
            )
            if (
                self.__caret_still_duration
                and (
                    self.__must_wait_before_supp_char
                    or self.__must_wait_before_tab
                    or self.__must_wait_before_enter
                    or self.__must_wait_before_backspace
                    or self.__must_wait_before_esc
                    or must_wait_before_last_char
                    or self.__must_wait_at_end
                )
            )
            else None
        )

        WAITING_EVENTS = (
            WinEvent.OBJECT_LOCATIONCHANGE,
            WinEvent.OBJECT_TEXTSELECTIONCHANGED,
            WinEvent.OBJECT_VALUECHANGE,
        )

        # Create event queue.
        CODE_UNITS_PER_FLUSH_HINT = 50
        capacity = (CODE_UNITS_PER_FLUSH_HINT + 1) * 2
        #i One more because a surrogate pair can lie on the edge and they're enqueued atomically. Down and up for every code unit.
        self.__events = wapi.new("INPUT[]", capacity)
        self.__num_events = 0

        # Send events batchwise.
        with self.__interference_tracker, caret_tracker or nullcontext():
            had_supp_char = False
            last_char_index_to_wait_before = len(text) - 1 if must_wait_before_last_char else None

            for index, code_point in enumerate(map(ord, text)):
                # Determine event properties.
                is_supp_char = code_point >= 0x10000

                if is_supp_char:
                    twenty_bits = code_point - 0x10000
                    high_surrogate = 0xD800 + (twenty_bits >> 10)
                    low_surrogate = 0xDC00 + (twenty_bits & 0b11_1111_1111)

                    vk = None
                    code_units = (high_surrogate, low_surrogate)
                    must_wait_before_vk = False
                elif 0xD800 <= code_point <= 0xDFFF:  # Surrogate.
                    raise ValueError("String contains surrogate. Text insertion aborted.")
                else:  # BMP character.
                    vk = _InsertSession.__VKS_BY_SELECT_ASCII_CODES.get(code_point)
                    code_units = (code_point,)
                    must_wait_before_vk = (
                        vk is not None
                        and (
                            (self.__must_wait_before_tab and vk == win32con.VK_TAB)
                            or (self.__must_wait_before_enter and vk == win32con.VK_RETURN)
                            or (self.__must_wait_before_backspace and vk == win32con.VK_BACK)
                            or (self.__must_wait_before_esc and vk == win32con.VK_ESCAPE)
                        )
                    )

                    is_printable = code_point >= 0x20 and code_point != 0x7F
                    if vk is None and not is_printable:
                        continue

                # Flush regularly, so checks aren't delayed for too long.
                effectively_flushed = False
                if self.__num_events >= CODE_UNITS_PER_FLUSH_HINT * 2:
                    effectively_flushed = self.__flush_queue()

                # Wait.
                if (
                    caret_tracker
                    and (
                        (self.__must_wait_before_supp_char and not had_supp_char and is_supp_char)
                        or must_wait_before_vk
                        or index == last_char_index_to_wait_before
                    )
                ):
                    if not effectively_flushed:
                        effectively_flushed = self.__flush_queue()
                    if effectively_flushed:
                        caret_tracker.require_silence(self.__caret_still_duration, WAITING_EVENTS)

                    if index == last_char_index_to_wait_before:
                        caret_tracker.reset_wait_start()

                if self.__must_yield_time:
                    self.__flush_queue()
                    self.__yield_to_target(self.__insertion_hwnd)

                # Enqueue.
                if vk is not None:
                    for up in (False, True):
                        self.__enqueue_vk_event(vk, up)
                else:
                    for code_unit in code_units:
                        for up in (False, True):
                            self.__enqueue_utf16_code_unit_event(code_unit, up)

                # Prevent OS session becoming unusable due to overly long runtime.
                if time.perf_counter() > self.__deadline:
                    raise TimeoutError("Text insertion took too long.")

                #
                had_supp_char = is_supp_char

            # Final batch.
            must_wait = (
                caret_tracker
                and self.__must_wait_at_end
                and self.__num_events > 1
                #i A single event must be an up-event, and waiting for an up-event shouldn't be necessary, because apps generally only insert characters on down-events.
            )
            may_shorten_wait = (
                must_wait_before_last_char  # Did wait before last character.
                and self.__num_events <= 2  # Single character or key.
            )

            effectively_flushed = self.__flush_queue()
            if effectively_flushed and must_wait:
                assert caret_tracker
                if may_shorten_wait:
                    # Shorten final wait to speed up `insert()` chains.
                    caret_tracker.wait(WAITING_EVENTS, timeout=self.__caret_still_duration)
                else:
                    caret_tracker.require_silence(self.__caret_still_duration, WAITING_EVENTS)

            #i If unbalanced down-events were possible after flushing, an emergency key-up would be needed in an `except` block after a large `try` block.

    def __yield_to_target(self, insertion_hwnd: CData) -> None:
        """Yields time to the target thread of the insertion.

        Blocks until the target thread is in its Win32 message loop again to give it time to process previous events, which may have been transferred to a UI-framework-specific message loop. This is relevant in Qt apps that tend to insert Unicode supplementary characters from later in the event stream before earlier BMP characters. It's *especially* relevant in the output pane of Qt-based gImageReader v3.4.3 where each new character initiates a text check; it's worse with longer text box contents. In Qt apps, without yielding, there can also be problems with the very first insertion of text containing supplementary characters after app start.
        """

        #i As per the `GetMessageW()` docs' remarks, sent messages are processed before all other events by default.

        kernel32.SetLastError(winerror.ERROR_SUCCESS)
        success = bool(user32.SendMessageTimeoutW(
            insertion_hwnd,
            win32con.WM_NULL,
            0,
            0,
            win32con.SMTO_BLOCK | user32.SMTO_ERRORONEXIT,
            #i `SMTO_ABORTIFHUNG` is indicated by failure return value, but not by a specific error code. Since this case is rare, a fitting exception message is hard to phrase and blocking seems natural in this case, the `SMTO_ABORTIFHUNG` flag isn't used.
            2000,  # ms
            wapi.NULL,
        ))
        if not success:
            last_error: int = kernel32.GetLastError()
            if last_error == winerror.ERROR_TIMEOUT:
                raise TimeoutError("Window took too long to react while yielding during text insertion.")
            else:
                raise ctypes.WinError(last_error)

    def __enqueue_vk_event(self, vk: int, up: bool) -> None:
        scancode = cast(int, win32api.MapVirtualKey(vk, user32.MAPVK_VK_TO_VSC_EX))  # Typed incompletely.
        has_e0_extended_scan_code = (scancode & 0xFF00) == 0xE000
        #i - AI GPT-5.2 thinks `wScan` must not contain the extended-prefix. But the docs for `KEYEVENTF_EXTENDEDKEY` seem to say otherwise.
        #i - We just ignore 0 on missing translation, because we don't use `KEYEVENTF_SCANCODE`, but primarily rely on the virtual-key code. The scancode is just for maximizing compatibility.

        event: Any = self.__events[self.__num_events]

        event.type = win32con.INPUT_KEYBOARD
        event.DUMMYUNIONNAME.ki.wVk = vk
        event.DUMMYUNIONNAME.ki.wScan = scancode

        flags = 0
        if has_e0_extended_scan_code:
            flags |= win32con.KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= win32con.KEYEVENTF_KEYUP
        event.DUMMYUNIONNAME.ki.dwFlags = flags

        event.DUMMYUNIONNAME.ki.time = 0
        event.DUMMYUNIONNAME.ki.dwExtraInfo = 0

        self.__num_events += 1

    def __enqueue_utf16_code_unit_event(self, code_unit: int, up: bool) -> None:
        event: Any = self.__events[self.__num_events]

        event.type = win32con.INPUT_KEYBOARD
        event.DUMMYUNIONNAME.ki.wVk = 0
        event.DUMMYUNIONNAME.ki.wScan = code_unit

        flags = win32con.KEYEVENTF_UNICODE
        if up:
            flags |= win32con.KEYEVENTF_KEYUP
        event.DUMMYUNIONNAME.ki.dwFlags = flags

        event.DUMMYUNIONNAME.ki.time = 0
        event.DUMMYUNIONNAME.ki.dwExtraInfo = 0

        self.__num_events += 1

    def __flush_queue(self) -> bool:
        """Checks on a best-effort basis whether the insertion target changed or there's an interfering state like pressed modifier keys, and then sends the queued events via `SendInput()`. Returns whether anything was sent."""

        if self.__num_events <= 0:
            return False

        # Check for various obstacles.
        self.__fill_gui_thread_info()

        insertion_hwnd = self.__get_insertion_hwnd(self.__gui_thread_info)
        if insertion_hwnd != self.__insertion_hwnd:
            raise RuntimeError(f"Window changed during text insertion. Insertion aborted. Original HWND: `{self.__insertion_hwnd}`. Displacing HWND: `{insertion_hwnd}`.")

        assert self.__interference_tracker
        if self.__interference_tracker.had((
            WinEvent.SYSTEM_MENUSTART,
            WinEvent.SYSTEM_MENUEND,
            WinEvent.SYSTEM_MENUPOPUPSTART,
            WinEvent.SYSTEM_MENUPOPUPEND,
        )):
            raise RuntimeError("Menu state changed. Text insertion aborted.")

        if self.__gui_thread_info.flags & win32con.GUI_INMOVESIZE:
            raise RuntimeError("Window is being moved or resized. Text insertion aborted.")

        if any(win32api.GetAsyncKeyState(key) < 0 for key in _MODIFIER_KEYS):
            raise RuntimeError("Modifier key held down. Text insertion aborted.")
        if any(win32api.GetAsyncKeyState(key) < 0 for key in _MOUSE_KEYS):
            raise RuntimeError("Mouse key held down. Text insertion aborted.")

        # Send queued events.
        num_events_sent: int = user32.SendInput(self.__num_events, self.__events, wapi.sizeof("INPUT"))
        if not num_events_sent:
            raise ctypes.WinError(kernel32.GetLastError())
        #i `SendInput()` can take a considerable amount of time (like > 1 s for long text). Its return time seems to correlate with the time where the foreground thread's message queue already received the events or will receive them briefly after (not guaranteed though). After that, text display can lag significantly as the app processes the messages in its queue (e.g., in Notepad and gImageReader).

        if num_events_sent != self.__num_events:
            # Best-effort emergency key-up. (Only as long as modifiers aren't involved additionally.)
            event: Any = None
            try:
                event = self.__events[num_events_sent - 1]
            except IndexError:
                pass

            extra_message = ""
            if event is not None and not (event.DUMMYUNIONNAME.ki.dwFlags & win32con.KEYEVENTF_KEYUP):
                time.sleep(0.1)  # Failure may just be transient.

                event.DUMMYUNIONNAME.ki.dwFlags |= win32con.KEYEVENTF_KEYUP
                success = bool(user32.SendInput(1, wapi.addressof(event), wapi.sizeof("INPUT")))
                if not success:
                    extra_message = " Emergency key-up also failed."

            #
            raise RuntimeError(f"Could only send {num_events_sent} of {self.__num_events} keyboard events.{extra_message}")

        # Reset event queue with regard to next flush.
        self.__num_events = 0

        #
        return True

    def __fill_gui_thread_info(self, may_retry: bool = False) -> None:
        """Fetches the `GUITHREADINFO` for the current foreground thread and saves it in the instance. Optionally retries repeatedly until a brief timeout elapsed to compensate for window activation in progress (observed null window handle when closing Notepad)."""

        deadline = time.perf_counter() + 0.050 if may_retry else None
        while True:
            success = bool(user32.GetGUIThreadInfo(0, self.__gui_thread_info))
            if not success:
                raise ctypes.WinError(kernel32.GetLastError())

            if self.__gui_thread_info.hwndActive:
                return
            elif not may_retry or (deadline and time.perf_counter() >= deadline):
                raise RuntimeError("No active window during text insertion.")

            time.sleep(0.005)

    def __get_insertion_hwnd(self, gui_thread_info: Any) -> CData:
        """Retrieves the specific `HWND` that'll also receive the `SendInput()` events from the OS."""

        return gui_thread_info.hwndFocus or gui_thread_info.hwndActive
        #i - `hwndActive` is always a top-level window.
        #i - `hwndFocus` can be `NULL` - like directly after launching Notepad.
        #i - For classical Win32 apps, `hwndFocus` is a control.
        #i - For UWP apps, which are hosted by `ApplicationFrameHost.exe`, `hwndActive` is the hosting top-level window from said .exe, and `hwndFocus` is the child window from the actual app process (another .exe) that renders the window's client area etc.
        #i - Some (many?) UI frameworks don't use Win32 child windows and both handles are the same.
