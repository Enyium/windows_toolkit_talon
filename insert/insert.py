"""
Reimplements Talon's `insert()` function and provides Talon settings that allow it to be configured.
"""

from contextlib import nullcontext
import ctypes
import time
from typing import TYPE_CHECKING

from talon import Context, Module, app, settings, ui

if app.platform == "windows" or TYPE_CHECKING:
    import win32api
    import win32con
    import winerror

    from ..lib.winapi import CType, kernel32, user32, wapi
    from ..win_events.tracker import Subfilter, WinEventTracker
    from ..win_events.constants import WinEvent, ObjectID, Role
else:
    raise NotImplementedError("Unsupported OS.")

_mod = Module()

_mod.tag(
    "si_insert__active",
    desc="Activates Smart Input's `insert()` override.",
)
_mod.setting(
    "si_insert__yield_time",
    type=bool,
    default=False,
    desc="Whether `insert()` waits before every character until the target window's message pump continues. This can be necessary to prevent the target window from mixing up characters, especially if significant work like spell checking is done after every new character.",
)
_mod.setting(
    "si_insert__caret_still_ms",
    type=float,
    default=55,
    desc="After sending the events for a chunk of text, incl. the last, these are the number of milliseconds the caret (text input cursor) position must not change until the target window is regarded as ready to receive further input. Note that, because of visual lag, the standstill may appear to be much shorter than it actually is eventwise; the criterion for determination of the value's magnitude is correct input. There could be some apps that don't report their carets in a manner currently recognizable by Smart Input. A value of 0 turns off all waiting for caret standstill. The concrete situations of waiting are controlled by some of the boolean settings.",
)
_mod.setting(
    "si_insert__caret_still_before_supp_char",
    type=bool,
    default=False,
    desc="Whether the setting `user.si_insert__caret_still_ms` applies when transitioning from a Unicode BMP character to a supplementary character. This can be necessary to prevent the characters from being mixed up.",
    #i In Qt apps, sending Unicode supplementary characters (those consisting of two surrogates in UTF-16) *after* BMP characters without waiting for caret standstill (e.g., by using a single `SendInput()` call) most often leads to the supplementary characters being placed *before* all or some of the BMP characters. This makes it necessary to start a new batch of events at every transition from BMP to supplementary characters and wait for caret standstill before continuing. (The other transition is unproblematic.) This technique alone prevents mixed up characters in many cases. Together with yielding, the problem disappears.
)
_mod.setting(
    "si_insert__caret_still_before_tab",
    type=bool,
    default=False,
    desc=r"""Whether the setting `user.si_insert__caret_still_ms` applies before `"\t"`.""",
)
_mod.setting(
    "si_insert__caret_still_before_enter",
    type=bool,
    default=False,
    desc=r"""Whether the setting `user.si_insert__caret_still_ms` applies before `"\n"`.""",
)
_mod.setting(
    "si_insert__caret_still_before_backspace",
    type=bool,
    default=True,
    desc=r"""Whether the setting `user.si_insert__caret_still_ms` applies before `"\b"`. This may make a preceding character like space reliable in dismissing suggestion overlays.""",
)
_mod.setting(
    "si_insert__caret_still_before_esc",
    type=bool,
    default=True,
    desc=r"""Whether the setting `user.si_insert__caret_still_ms` applies before `"\N{ESC}"`. This may make Esc reliable in dismissing suggestion overlays.""",
)
_mod.setting(
    "si_insert__caret_still_at_end",
    type=bool,
    default=True,
    desc="Whether the setting `user.si_insert__caret_still_ms` applies at the end of the text insertion. This is useful to ensure a settled state for follow-up voice commands.",
)

_ctx = Context()
_ctx.matches = r"""
tag: user.si_insert__active
"""

_INSERTION_TIMEOUT = 30
"""Seconds until a single insertion session is aborted."""

_MODIFIER_KEYS = (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_MENU, win32con.VK_LWIN, win32con.VK_RWIN)
_MOUSE_KEYS = (win32con.VK_LBUTTON, win32con.VK_RBUTTON, win32con.VK_MBUTTON, win32con.VK_XBUTTON1, win32con.VK_XBUTTON2)


@_ctx.action_class("main")
class _MainActions:
    def insert(text: str):
        """A reimplementation and replacement of Talon's original action that is much faster, doesn't have issues with dead keys, and is more resilient against interference.

        Characters are sent as themselves rather than their respective key presses, which are keyboard-layout-dependent. This also comes with independence from the caps lock state. In some apps/web apps, inserting characters with this override is not a working alternative to pressing them using Talon's `key()` action, which Talon's original `insert()` seems to use.

        Real key events are only simulated for the following keys:

        - Tab (`\t`) - As with Talon's `insert()`, you must be careful not to accidentally accept editor suggestions. For most code use cases, only ever inserting `\t` at the start of a line or after other whitespace should suffice. If you work with TSV (tab-separated values) or something like that, you may need to turn off automatically appearing suggestion overlays completely.
        - Enter (`\n`) - As for Talon's `insert()`, you should turn off accepting suggestions with Enter altogether in every app, because characters triggering these overlays at the end of lines are basically unavoidable. See also Smart Input repository's readme.
        - Backspace (`\b`) - Finalizing the current word with, e.g., a space character and deleting it again can dismiss a suggestion overlay, but race conditions may cause problems in certain apps. The setting `user.si_insert__caret_still_before_backspace` may help.
        - Esc (`\N{ESC}`, `\x1b`) - Can dismiss a suggestion overlay to prevent confirming it, but race conditions may cause problems in certain apps. The setting `user.si_insert__caret_still_before_esc` may help. In general, success depends too much on app settings and IDE state (find box incl. its highlights, etc.).

        You can use the following tags and settings to configure the behavior of the action:

        - `user.si_insert__active`
        - `user.si_insert__yield_time`
        - `user.si_insert__caret_still_ms`
        - `user.si_insert__caret_still_before_supp_char`
        - `user.si_insert__caret_still_before_tab`
        - `user.si_insert__caret_still_before_enter`
        - `user.si_insert__caret_still_before_backspace`
        - `user.si_insert__caret_still_before_esc`
        - `user.si_insert__caret_still_at_end`
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

    def __init__(self):
        self.__start_time = None

        self.__must_yield_time = settings.get("user.si_insert__yield_time")
        self.__caret_still_duration = max(0, settings.get("user.si_insert__caret_still_ms") / 1000)
        self.__wait_before_supp_char = settings.get("user.si_insert__caret_still_before_supp_char")
        self.__wait_before_tab = settings.get("user.si_insert__caret_still_before_tab")
        self.__wait_before_enter = settings.get("user.si_insert__caret_still_before_enter")
        self.__wait_before_backspace = settings.get("user.si_insert__caret_still_before_backspace")
        self.__wait_before_esc = settings.get("user.si_insert__caret_still_before_esc")
        self.__wait_at_end = settings.get("user.si_insert__caret_still_at_end")

        self.__events = None
        self.__num_events = 0

        self.__gui_thread_info = wapi.new("GUITHREADINFO *", {"cbSize": wapi.sizeof("GUITHREADINFO")})
        self.__insertion_toplevel_hwnd = None
        self.__insertion_hwnd = None
        self.__menu_active_at_start = False

    def __call__(self, text):
        # Convert text to UTF-16 code units.
        utf16le_bytes = text.encode("utf-16-le", errors="surrogatepass")
        code_units = memoryview(utf16le_bytes).cast("@H")  # Native-endian unsigned short.
        if not _is_well_formed_utf16(code_units):
        #i Doing this check in the event-sending loop could lead to unfinished input.
            raise ValueError("Malformed UTF-16.")

        # Establish windows.
        self.__fill_gui_thread_info()

        self.__insertion_toplevel_hwnd = self.__gui_thread_info.hwndActive
        self.__insertion_hwnd = self.__get_insertion_hwnd(False)

        active_window = ui.active_window()
        if wapi.cast("HWND", active_window.id) != self.__insertion_toplevel_hwnd:
            raise RuntimeError(f"Talon and utilized WinAPI disagree about active window during text insertion. Talon: `{active_window}` (ID 0x{active_window.id:X}). `GUITHREADINFO.hwndActive`: {self.__insertion_toplevel_hwnd}.")

        # Limit insertion if menu active.
        if self.__gui_thread_info.flags & (win32con.GUI_SYSTEMMENUMODE | win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE):
            self.__menu_active_at_start = True
            if len(text) > 1:
                raise RuntimeError("Received more than one character to insert while menu is active. Text insertion aborted.")
        #i See also `_flush_queue()`.

        # Create event queue.
        capacity = len(code_units) * 2  # Down and up for every code unit.
        self.__events = wapi.new("INPUT[]", capacity)
        self.__num_events = 0

        # Send events batchwise.
        self.__start_time = time.perf_counter()

        with (
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
            )
            if (
                self.__caret_still_duration
                and (
                    self.__wait_before_supp_char
                    or self.__wait_before_tab
                    or self.__wait_before_enter
                    or self.__wait_before_backspace
                    or self.__wait_before_esc
                    or self.__wait_at_end
                )
            )
            else nullcontext()
            as caret_tracker
            #i Note that apps' UIs and their signaling of win events may not be in sync. E.g., in VS Code v1.109.5, the sent text may already be presented while `OBJECT_LOCATIONCHANGE` events continue to arrive for a moment, making caret waits appear longer. In Notepad of Windows 11 Home 25H2, the arrival of win events may already have ceased while text still continues to appear, visually smoothing out caret waits that were actually longer than they appeared to be. (Printing on win event reception is better than using AccEvent to understand this effect.)
        ):
            WAITING_EVENTS = (
                WinEvent.OBJECT_LOCATIONCHANGE,
                WinEvent.OBJECT_TEXTSELECTIONCHANGED,
                WinEvent.OBJECT_VALUECHANGE,
            )

            did_enqueue = False
            had_surrogate = False

            for code_unit in code_units:
                # Identify event.
                vk = _InsertSession.__VKS_BY_SELECT_ASCII_CODES.get(code_unit)
                is_vk_event = vk is not None

                is_printable = code_unit >= 0x20 and code_unit != 0x7F

                if not (is_vk_event or is_printable):
                    continue

                is_surrogate = code_unit >= 0xD800 and code_unit <= 0xDFFF

                # Regular flushing, so checks aren't delayed for too long.
                #TODO: This can split surrogate pairs, which should be avoided. Probably give up early UTF-16 conversion and do it per character. Then also ensure that all four events of a supplementary character are flushed at once.
                if self.__num_events >= 50 * 2:
                    self.__flush_queue()

                # Wait.
                must_wait = (
                    caret_tracker
                    and (
                        (self.__wait_before_supp_char and not had_surrogate and is_surrogate)
                        or (self.__wait_before_tab and vk == win32con.VK_TAB)
                        or (self.__wait_before_enter and vk == win32con.VK_RETURN)
                        or (self.__wait_before_backspace and vk == win32con.VK_BACK)
                        or (self.__wait_before_esc and vk == win32con.VK_ESCAPE)
                    )
                    and did_enqueue
                )
                if must_wait:
                    self.__flush_queue()
                    caret_tracker.require_silence(self.__caret_still_duration, WAITING_EVENTS)

                if self.__must_yield_time:
                    self.__flush_queue()
                    self.__yield_to_target(self.__insertion_hwnd)

                # Enqueue.
                for up in (False, True):
                    if is_vk_event:
                        self.__enqueue_vk_event(vk, up)
                    else:
                        self.__enqueue_utf16_code_unit_event(code_unit, up)

                did_enqueue = True

                # Prevent OS session becoming unusable due to overly long runtime.
                if time.perf_counter() - self.__start_time >= _INSERTION_TIMEOUT:
                    raise TimeoutError("Text insertion took too long.")

                #
                had_surrogate = is_surrogate

            # Final batch.
            must_wait = (
                caret_tracker
                and self.__wait_at_end
                and self.__num_events > 1
                #i A single event must be an up-event, and waiting for an up-event shouldn't be necessary, because apps generally only insert characters on down-events.
            )
            self.__flush_queue()
            if must_wait:
                caret_tracker.require_silence(self.__caret_still_duration, WAITING_EVENTS)

    def __yield_to_target(self, insertion_hwnd: CType):
        """Yields time to the target thread of the insertion.

        Blocks until the target thread is in its Win32 message loop again to give it time to process previous events, which may have been transferred to a UI-framework-specific message loop. This is relevant in Qt apps that tend to insert Unicode supplementary characters from later in the event stream before earlier BMP characters. It's *especially* relevant in the output pane of Qt-based gImageReader v3.4.3 where each new character initiates a text check; it's worse with longer text box contents. In Qt apps, without yielding, there can also be problems with the very first insertion of text containing supplementary characters after app start.
        """

        #i As per the `GetMessageW()` docs' remarks, sent messages are processed before all other events by default.

        kernel32.SetLastError(winerror.ERROR_SUCCESS)
        success = user32.SendMessageTimeoutW(
            insertion_hwnd,
            win32con.WM_NULL,
            0,
            0,
            win32con.SMTO_BLOCK | user32.SMTO_ERRORONEXIT,
            #i `SMTO_ABORTIFHUNG` is indicated by failure return value, but not by a specific error code. Since this case is rare, a fitting exception message is hard to phrase and blocking seems natural in this case, the `SMTO_ABORTIFHUNG` flag isn't used.
            2000,  # ms
            wapi.NULL,
        )
        if not success:
            last_error = kernel32.GetLastError()
            if last_error == winerror.ERROR_TIMEOUT:
                raise TimeoutError("Window took too long to react while yielding during text insertion.")
            else:
                raise ctypes.WinError(last_error)

    def __enqueue_vk_event(self, vk: int, up: bool):
        scancode = win32api.MapVirtualKey(vk, user32.MAPVK_VK_TO_VSC_EX)
        has_e0_extended_scan_code = (scancode & 0xFF00) == 0xE000
        #i - AI GPT-5.2 thinks `wScan` must not contain the extended-prefix. But the docs for `KEYEVENTF_EXTENDEDKEY` seem to say otherwise.
        #i - We just ignore 0 on missing translation, because we don't use `KEYEVENTF_SCANCODE`, but primarily rely on the virtual-key code. The scancode is just for maximizing compatibility.

        event = self.__events[self.__num_events]

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

    def __enqueue_utf16_code_unit_event(self, code_unit: int, up: bool):
        event = self.__events[self.__num_events]

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

    def __flush_queue(self):
        if self.__num_events <= 0:
            return

        #TODO: Maybe implement `_emergency_keyup()` function and use it in a `finally` block, perhaps in `__call__()`. Continue with regular `insert()` action, so user stays able to insert text. But only if the exception wasn't flagged as okay to abort insertion.
        #TODO: Use `WinEventTracker.had()` to check whether the menu was focused (or focus in general was changed) since the last `SendInput()` call.
        # Check for various obstacles. (Exceptions could theoretically lead to key-down without key-up event. Severity yet unknown.)
        self.__fill_gui_thread_info()

        insertion_hwnd = self.__get_insertion_hwnd()
        if insertion_hwnd != self.__insertion_hwnd:
            raise RuntimeError(f"Window changed during text insertion. Insertion aborted. Original: `{self.__insertion_hwnd}`. Displacing: `{insertion_hwnd}`.")

        menu_active = self.__gui_thread_info.flags & (win32con.GUI_SYSTEMMENUMODE | win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE)
        if not self.__menu_active_at_start and menu_active:
            raise RuntimeError("Menu appeared. Text insertion aborted.")
        #i This technique only works for traditional Win32 menus incl. a window's system menu. The universal way would be to check whether `talon.windows.ax.get_focused_element().control_type` is `"MenuBar"` or `"MenuItem"`. But unfortunately, this API is very slow and would introduce a delay of up to about 83 ms before most flushes, according to the author's measurements.

        if self.__gui_thread_info.flags & win32con.GUI_INMOVESIZE:
            raise RuntimeError("Window is being moved or resized. Text insertion aborted.")

        if any(win32api.GetAsyncKeyState(key) < 0 for key in _MODIFIER_KEYS):
            raise RuntimeError("Modifier key held down. Text insertion aborted.")
        if any(win32api.GetAsyncKeyState(key) < 0 for key in _MOUSE_KEYS):
            raise RuntimeError("Mouse key held down. Text insertion aborted.")

        # Send queued events.
        num_events_sent = user32.SendInput(self.__num_events, self.__events, wapi.sizeof("INPUT"))
        if not num_events_sent:
            raise ctypes.WinError(kernel32.GetLastError())
        if num_events_sent != self.__num_events:
            raise RuntimeError(f"Could only send {num_events_sent} of {self.__num_events} keyboard events.")
        #i `SendInput()` can take a considerable amount of time (like > 1 s for long text). Its return time seems to correlate with the time where the foreground thread's message queue already received the events or will receive them briefly after (not guaranteed though). After that, text display can lag significantly as the app processes the messages in its queue (e.g., in Notepad and gImageReader).

        # Reset event queue with regard to next flush.
        self.__num_events = 0

    def __fill_gui_thread_info(self):
        """Fetches the `GUITHREADINFO` for the current foreground thread and saves it in the instance."""

        success = user32.GetGUIThreadInfo(0, self.__gui_thread_info)
        if not success:
            raise ctypes.WinError(kernel32.GetLastError())

    def __get_insertion_hwnd(self, is_inserting: bool = True) -> CType:
        """Checks and retrieves the specific `HWND` from the current `GUITHREADINFO` that'll also receive the `SendInput()` events from the OS."""

        hwnd = self.__gui_thread_info.hwndFocus or self.__gui_thread_info.hwndActive
        #i - `hwndActive` is always a top-level window.
        #i - For classical Win32 apps, `hwndFocus` is a control.
        #i - For UWP apps, which are hosted by `ApplicationFrameHost.exe`, `hwndActive` is the hosting top-level window from said .exe, and `hwndFocus` is the child window from the actual app process (another .exe) that renders the window's client area etc.
        #i - For some (many?) UI frameworks without Win32 child windows, both handles are the same.

        if hwnd == wapi.NULL:
            raise RuntimeError(
                "Missing window during text insertion."
                + (" Insertion aborted." if is_inserting else "")
            )

        return hwnd


def _is_well_formed_utf16(code_units):
    had_high_surrogate = False
    for code_unit in code_units:
        is_low_surrogate = code_unit >= 0xDC00 and code_unit <= 0xDFFF
        if (
            (not had_high_surrogate and is_low_surrogate)
            or (had_high_surrogate and not is_low_surrogate)
        ):
            return False

        had_high_surrogate = code_unit >= 0xD800 and code_unit <= 0xDBFF

    return not had_high_surrogate
