"""
Reimplements Talon's `insert()` function and provides Talon settings that allow it to be configured.
"""

from contextlib import nullcontext
import ctypes
import time
from typing import TYPE_CHECKING

from talon import Context, Module, actions, app, settings, ui

if app.platform == "windows" or TYPE_CHECKING:
    import win32api
    import win32con

    from .lib.winapi import kernel32, user32, wapi
else:
    raise NotImplementedError("Unsupported OS.")

from .win_events.tracker import Subfilter, WinEventTracker
from .win_events.constants import WinEvent, ObjectID

_mod = Module()

_mod.tag(
    "smart_input_insert_active",
    desc="Activates Smart Input's `insert()` override.",
)
_mod.setting(
    "caret_standstill_ms_before_ready",
    type=float,
    default=0.0,
    desc="After sending the events for a chunk of text, incl. the last, these are the number of milliseconds the caret (text input cursor) position must not change until the target window is recognized as ready to receive further input. Note that, because of visual lag, the standstill may appear to be much shorter than it actually is eventwise; the criterion for determination of the value's magnitude is correct input. Not every app reports its carets in a manner currently recognizable by Smart Input (e.g., Windows Terminal in Windows 11 Home 24H2). A value of 0 expressly turns off all waiting for caret standstill.",
)
_mod.setting(
    "char_pause_ms",
    type=float,
    default=0.0,
    desc="Milliseconds to sleep per character sent as itself, as opposed to its respective keys.",
)
# _mod.setting(
#     "abort_insert_merely_on_app_change",
#     type=bool,
#     default=False,
#     desc="Whether `insert()` should only abort when the active app is changed. Otherwise, it will abort when the active window is changed. This is useful when a suggestion window would otherwise cause abortion.",
# )
#i Setting `user.abort_insert_merely_on_app_change` unavailable for now. More information is needed what window activation is actually happening in the rare case that input is aborted by it (experienced in VS Code and gImageReader, but VS Code's suggestion overlays aren't Win32 windows, let alone top-level windows).

_ctx = Context()
_ctx.matches = r"""
tag: user.smart_input_insert_active
"""

_INSERTION_TIMEOUT = 30
"""Seconds until a single insertion session is aborted."""


@_ctx.action_class("main")
class _MainActions:
    def insert(text: str):
        """A reimplementation and replacement of Talon's original function that won't cause problems with dead keys, is more resilient against interference, and is much faster.

        Characters are sent as themselves and not as their respective key presses, which are keyboard-layout-dependent. This also comes with independence from the caps lock state, and in some (web) apps, inserting characters is not a working alternative to pressing them using Talon's `key()` function, which Talon's original `insert()` seems to use. So, voice commands must be dedicated to either `key()` or `insert()`.

        The only real key events simulated are for these keys:

        - Tab (`\t`, `\N{TAB}`) - As with Talon's `insert()`, you must be careful not to accidentally accept editor suggestions. For most code use cases, only ever inserting `\t` at the start of a line or after other whitespace should suffice. If you work with TSV (tab-separated values) or something like that, you may need to turn off automatically appearing suggestion overlays completely.
        - Enter (`\n`, `\N{NEW LINE}`) - As for Talon's `insert()`, you should turn off accepting suggestions with Enter altogether in every app, because characters triggering these overlays at the end of lines are basically unavoidable. See also Smart Input repository's readme.
        - Esc (`\x1b`, `\N{ESC}`, `\N{ESCAPE}`) - Can dismiss a suggestion overlay to prevent confirming it, but race conditions may cause problems in certain apps. In general, success also depends too much on app settings and IDE state (find box incl. its highlights, etc.).
        - Backspace (`\b`, `\N{BS}`, `\N{BACKSPACE}`) - Finalizing the current word with a space character and deleting it again can dismiss a suggestion overlay, but race conditions may cause problems in certain apps.

        Talon's setting `key_hold` still applies for those keys. Additionally, you can use the settings `user.caret_standstill_ms_before_ready`, and `user.char_pause_ms`.
        """
        #, and `user.abort_insert_merely_on_app_change`

        _InsertSession()(text)


class _InsertSession:
    _VKS_BY_SELECT_ASCII_CODES = {
        # Keyboard-layout-invariant virtual-key codes that don't work with `SendInput()`'s `KEYEVENTF_UNICODE` flag.
        0x08: win32con.VK_BACK,
        0x09: win32con.VK_TAB,
        0x0A: win32con.VK_RETURN,
        0x1B: win32con.VK_ESCAPE,
    }

    def __init__(self):
        self._start_time = None

        self._key_hold_duration = max(0, settings.get("key_hold") / 1000)
        self._caret_standstill_duration = max(0, settings.get("user.caret_standstill_ms_before_ready") / 1000)
        self._char_pause_duration = max(0, settings.get("user.char_pause_ms") / 1000)
        self._abort_on_window_xor_app_change = True #not settings.get("user.abort_insert_merely_on_app_change")
        #i `key_wait` only applies when modifiers are involved, which isn't the case here.

        self._events = None
        self._num_events = 0

        active_window = ui.active_window()
        self._insertion_toplevel_hwnd = active_window.id
        self._insertion_pid = active_window.app.pid

        self._gui_thread_info = wapi.new("GUITHREADINFO *", {"cbSize": wapi.sizeof("GUITHREADINFO")})
        self._menu_active_at_start = False

    def __call__(self, text):
        # Convert text to UTF-16 code units.
        utf16le_bytes = text.encode("utf-16-le", errors="surrogatepass")
        code_units = memoryview(utf16le_bytes).cast("@H")  # Native-endian unsigned short.
        if not _is_well_formed_utf16(code_units):
        #i Doing this check in the event-sending loop could lead to unfinished input.
            raise ValueError("Malformed UTF-16.")

        # Limit insertion if menu active.
        self._fill_gui_thread_info()
        if self._gui_thread_info.flags & (win32con.GUI_SYSTEMMENUMODE | win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE):
            self._menu_active_at_start = True
            if len(text) > 1:
                raise RuntimeError("Received more than one character to insert while menu is active. Text insertion aborted.")
        #i See also `_flush()`.

        # Create event queue.
        capacity = len(code_units) * 2  # Down and up for every code unit.
        self._events = wapi.new("INPUT[]", capacity)
        self._num_events = 0

        # Send events chunkwise.
        self._start_time = time.perf_counter()

        with (
            WinEventTracker(
                Subfilter(
                    WinEvent.OBJECT_LOCATIONCHANGE,
                    #i In Windows Terminal, both `OBJECT_LOCATIONCHANGE` and `CONSOLE_CARET` didn't work. In `conhost.exe`, they did. (Windows 11 Home 24H2)
                    object_id=ObjectID.CARET,
                ),
                inclusive_ancestor_hwnd=self._insertion_toplevel_hwnd,
                timeout=_INSERTION_TIMEOUT,
            )
            if self._caret_standstill_duration
            else nullcontext()
            as caret_tracker
            #i Note that apps' UIs and their signaling of win events may not be in sync. E.g., in VS Code v1.109.5, the sent text may already be presented while `OBJECT_LOCATIONCHANGE` events continue to arrive for a moment, making caret waits appear longer. In Notepad of Windows 11 Home 25H2, the arrival of win events may already have ceased while text still continues to appear, visually smoothing out caret waits that were actually longer than they appeared to be. (Printing on win event reception is better than using AccEvent to understand this effect.)
        ):
            had_surrogate = False
            for code_unit in code_units:
                vk = _InsertSession._VKS_BY_SELECT_ASCII_CODES.get(code_unit)  # Never 0.
                is_vk_event = bool(vk)

                is_printable = code_unit >= 0x20 and code_unit != 0x7F

                if not (is_vk_event or is_printable):
                    continue

                is_surrogate = code_unit >= 0xD800 and code_unit <= 0xDFFF

                for down in (True, False):
                    if down:
                        #TODO: WITH SUITABLE TEST APP: With regard to suggestion windows, it could be necessary to also wait for caret standstill before `\N{esc}`, `\t` and `\n` (suggestions are confirmed with Tab and/or Enter, depending on the app and its settings). But a test app would be needed that has suggestion windows, reports its caret, and benefits from waiting for caret standstill. Depending on the settings, these additional pauses could undesirably add to `key_hold` pauses.
                        if caret_tracker and is_surrogate and not had_surrogate:
                            self._flush()
                            caret_tracker.require_silence(self._caret_standstill_duration)
                            #i In Qt apps, sending Unicode supplementary characters (those consisting of two surrogates) *after* BMP characters without waiting for caret standstill (e.g., by using a single `SendInput()` call) most often leads to the supplementary characters being placed *before* all or some of the BMP characters. This is why we start a new chunk at every transition from BMP to supplementary characters and wait for caret standstill before continuing. (The other transition is unproblematic.)
                    else:
                        if is_vk_event:
                            if self._key_hold_duration:
                                self._flush()
                                actions.sleep(self._key_hold_duration)
                        else:
                            if self._char_pause_duration:
                                self._flush()
                                actions.sleep(self._char_pause_duration)

                    if is_vk_event:
                        self._push_vk_event(vk, not down)
                    else:
                        self._push_utf16_code_unit_event(code_unit, not down)

                if time.perf_counter() - self._start_time >= _INSERTION_TIMEOUT:
                    raise RuntimeError("Text insertion took too long.")

                had_surrogate = is_surrogate

            must_wait = self._caret_standstill_duration and self._num_events > 1
            #i A single event must be an up-event, and waiting for an up-event shouldn't be necessary, because apps generally only insert characters on down-events.
            self._flush()
            if must_wait:
                caret_tracker.require_silence(self._caret_standstill_duration)

    def _push_vk_event(self, vk: int, up: bool):
        scancode = win32api.MapVirtualKey(vk, user32.MAPVK_VK_TO_VSC_EX)
        has_e0_extended_scan_code = (scancode & 0xFF00) == 0xE000
        #i - AI GPT-5.2 thinks `wScan` must not contain the extended-prefix. But the docs for `KEYEVENTF_EXTENDEDKEY` seem to say otherwise.
        #i - We just ignore 0 on missing translation, because we don't use `KEYEVENTF_SCANCODE`, but primarily rely on the virtual-key code. The scancode is just for maximizing compatibility.

        event = self._events[self._num_events]

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

        self._num_events += 1

    def _push_utf16_code_unit_event(self, code_unit: int, up: bool):
        event = self._events[self._num_events]

        event.type = win32con.INPUT_KEYBOARD
        event.DUMMYUNIONNAME.ki.wVk = 0
        event.DUMMYUNIONNAME.ki.wScan = code_unit

        flags = win32con.KEYEVENTF_UNICODE
        if up:
            flags |= win32con.KEYEVENTF_KEYUP
        event.DUMMYUNIONNAME.ki.dwFlags = flags

        event.DUMMYUNIONNAME.ki.time = 0
        event.DUMMYUNIONNAME.ki.dwExtraInfo = 0

        self._num_events += 1

    def _flush(self):
        if self._num_events <= 0:
            return

        #TODO: Maybe implement `_emergency_keyup()` function and use it in a `finally` block, perhaps in `__call__()`. (Continue with regular `insert()` action, so user stays able to insert text.)
        #TODO: Use `WinEventTracker.had()` to check whether the menu was focused (or focus in general was changed) since the last `SendInput()` call.
        # Check for various obstacles. (Exceptions could theoretically lead to key-down without key-up event. Severity yet unknown.)
        if self._abort_on_window_xor_app_change:
            active_window = ui.active_window()
            if active_window.id != self._insertion_toplevel_hwnd:
                raise RuntimeError(f"Active window changed during text insertion. Insertion aborted. Displacing window and app: `{active_window}` (ID {hex(active_window.id)}).")
        else:
            active_window = ui.active_window()
            if active_window.app.pid != self._insertion_pid:
                raise RuntimeError(f"Active app changed during text insertion. Insertion aborted. Displacing window and app: `{active_window}` (ID {hex(active_window.id)}).")

        self._fill_gui_thread_info()
        menu_active = self._gui_thread_info.flags & (win32con.GUI_SYSTEMMENUMODE | win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE)
        if not self._menu_active_at_start and menu_active:
            raise RuntimeError("Menu appeared. Text insertion aborted.")
        #i This technique only works for traditional Win32 menus incl. a window's system menu. The universal way would be to check whether `talon.windows.ax.get_focused_element().control_type` is `"MenuBar"` or `"MenuItem"`. But unfortunately, this API is very slow and would introduce a delay of up to about 83 ms before most flushes, according to the author's measurements.

        if self._gui_thread_info.flags & win32con.GUI_INMOVESIZE:
            raise RuntimeError("Window is being moved or resized. Text insertion aborted.")

        if (
            win32api.GetAsyncKeyState(win32con.VK_CONTROL) < 0
            or win32api.GetAsyncKeyState(win32con.VK_SHIFT) < 0
            or win32api.GetAsyncKeyState(win32con.VK_MENU) < 0  # Alt.
            or win32api.GetAsyncKeyState(win32con.VK_LWIN) < 0
            or win32api.GetAsyncKeyState(win32con.VK_RWIN) < 0
        ):
            raise RuntimeError("Modifier key held down. Text insertion aborted.")

        # Send queued events.
        num_events_sent = user32.SendInput(
            self._num_events, self._events, wapi.sizeof("INPUT")
        )
        if not num_events_sent:
            raise ctypes.WinError(kernel32.GetLastError())
        if num_events_sent != self._num_events:
            raise RuntimeError("Could only send some, but not all keyboard events.")

        # Reset event queue with regard to next flush.
        self._num_events = 0

    def _fill_gui_thread_info(self):
        success = user32.GetGUIThreadInfo(0, self._gui_thread_info)
        if not success:
            raise ctypes.WinError(kernel32.GetLastError())


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
