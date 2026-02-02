import ctypes
import time
from typing import TYPE_CHECKING

from talon import Context, Module, actions, app, settings, ui

if app.platform == "windows" or TYPE_CHECKING:
    from ctypes import wintypes

    import pywintypes
    import win32api
    import win32con
    import win32gui
    import winerror

    from .lib.winapi import INPUT, GUITHREADINFO, MAPVK_VK_TO_VSC_EX, SMTO_ERRORONEXIT, user32
else:
    raise NotImplementedError("Unsupported OS.")

_mod = Module()

_mod.setting(
    "char_pause_ms",
    type=float,
    default=0.0,
    desc="Milliseconds to sleep per character directly sent as itself (as opposed to its respective keys). The setting `user.stable_caret_ms_until_idle` may prevent the per-character pauses to apply.",
)
_mod.setting(
    "stable_caret_ms_until_idle",
    type=float,
    default=0.0,
    desc="Milliseconds the caret (text input cursor) coordinates must not change until the target window is recognized as ready to receive further input. Not every app reports its carets in a performantly queryable manner. If it doesn't or stops reporting them mid-insertion, `user.char_pause_ms` is used for the rest of the insertion; but this may come too late for certain text. A setting value of 0 expressly turns the feature off.",
)
# _mod.setting(
#     "abort_insert_merely_on_app_change",
#     type=bool,
#     default=False,
#     desc="Whether `insert()` should only abort when the active app is changed. Otherwise, it will abort when the active window is changed. This is useful when a suggestion window would otherwise cause abortion.",
# )
#i Setting `user.abort_insert_merely_on_app_change` unavailable for now. More information is needed what window activation is actually happening in the rare case that input is aborted by it (experienced in VS Code, but its suggestion overlays aren't Win32 windows, let alone top-level windows).

_ctx = Context()

#. Seconds until insertion is aborted.
_INSERTION_TIMEOUT = 30


@_ctx.action_class("main")
class _MainActions:
    def insert(text: str):
        """A reimplementation and replacement of Talon's original function that won't cause problems with dead keys, is more resilient against interference, and is often much faster.

        Characters are sent as themselves, and not as their respective key presses, which are keyboard-layout-dependent. This also comes with independence from the caps lock state. The only real key events simulated are for these keys:

        - Tab (`\t`, `\N{TAB}`) - As with Talon's `insert()`, you must be careful not to accidentally accept editor suggestions. For most code use cases, only ever inserting `\t` at the start of a line or after other whitespace should suffice. If you work with TSV (tab-separated values) or something like that, you may need to turn off automatically appearing suggestion overlays completely.
        - Enter (`\n`, `\N{NEW LINE}`) - As for Talon's `insert()`, you should turn off accepting suggestions with Enter altogether in every app, because characters triggering these overlays at the end of lines are basically unavoidable. See also Smart Input repository's readme.
        - Esc (`\x1b`, `\N{ESC}`, `\N{ESCAPE}`) - Can dismiss a suggestion overlay to prevent confirming it, but race conditions may cause problems in certain apps. In general, success also depends too much on app settings and IDE state (find box incl. its highlights, etc.).
        - Backspace (`\b`, `\N{BS}`, `\N{BACKSPACE}`) - Finalizing the current word with a space character and deleting it again can dismiss a suggestion overlay, but race conditions may cause problems in certain apps.

        Talon's setting `key_hold` still applies for those keys. Additionally, you can use the settings `user.char_pause_ms`, and `user.stable_caret_ms_until_idle`.
        """
        #, and `user.abort_insert_merely_on_app_change`

        _InsertSession()(text)


class _InsertSession:
    _VKS_BY_SELECT_ASCII_CODES = {
        # Keyboard-layout-invariant virtual-key codes that don't work with the `KEYEVENTF_UNICODE` flag.
        0x08: win32con.VK_BACK,
        0x09: win32con.VK_TAB,
        0x0A: win32con.VK_RETURN,
        0x1B: win32con.VK_ESCAPE,
    }

    def __init__(self):
        self._start_time = None

        self._key_hold_duration = settings.get("key_hold") / 1000
        self._char_pause_duration = settings.get("user.char_pause_ms") / 1000
        self._stable_caret_duration = settings.get("user.stable_caret_ms_until_idle") / 1000
        self._must_wait_for_stable_caret = self._stable_caret_duration > 0
        self._abort_on_window_xor_app_change = True #not settings.get("user.abort_insert_merely_on_app_change")
        #i `key_wait` only applies when modifiers are involved, which isn't the case here.

        self._events = None
        self._num_events = 0

        active_window = ui.active_window()
        self._insertion_hwnd = active_window.id
        self._insertion_pid = active_window.app.pid

        self._gui_thread_info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))

    def __call__(self, text):
        # Text to UTF-16 code units.
        utf16le_bytes = text.encode("utf-16-le", errors="surrogatepass")
        code_units = memoryview(utf16le_bytes).cast("@H")  # Native-endian unsigned short.
        if not _is_well_formed_utf16(code_units):
            raise ValueError("Malformed UTF-16.")

        # Create event queue.
        if not self._must_wait_for_stable_caret and self._char_pause_duration > 0:
            # At most last up and next down.
            capacity = 2
        else:
            # Down and up for every code unit.
            capacity = len(code_units) * 2
        self._events = (INPUT * capacity)()
        self._num_events = 0

        # Send events.
        self._start_time = time.perf_counter()
        had_surrogate = False

        for i, code_unit in enumerate(code_units):
            vk = _InsertSession._VKS_BY_SELECT_ASCII_CODES.get(code_unit)  # Never 0.
            is_vk_event = bool(vk)

            is_printable = code_unit >= 0x20 and code_unit != 0x7F

            if not (is_vk_event or is_printable):
                continue

            is_surrogate = code_unit >= 0xD800 and code_unit <= 0xDFFF

            for up in [False, True]:
                if not up:
                    #TODO: WITH SUITABLE TEST APP: With regard to suggestion windows, it could be necessary to also wait for caret stabilization before `\N{esc}`, `\t` and `\n` (suggestions are confirmed with Tab and/or Enter, depending on the app and its settings). But a test app would be needed that has suggestion windows, reports its caret, and benefits from caret stabilization. Depending on the settings, these additional pauses could undesirably add to `key_hold` pauses.
                    if self._must_wait_for_stable_caret and is_surrogate and not had_surrogate:
                        self._flush()
                        self._wait_for_stable_caret()
                        #i In Qt apps, sending non-BMP characters (those consisting of surrogates) *after* BMP characters (<= U+FFFF) without waiting until the caret has stabilized (e.g., by using a single `SendInput()` call) can lead to the non-BMP characters being placed *before* the BMP characters. This is why we start a new chunk at every transition from BMP to non-BMP characters and wait for the caret to stabilize before continuing. (The other transition is unproblematic.)
                else:
                    if is_vk_event:
                        if self._key_hold_duration > 0:
                            self._flush()
                            actions.sleep(self._key_hold_duration)
                    else:
                        if not self._must_wait_for_stable_caret and self._char_pause_duration > 0:
                            self._flush()
                            actions.sleep(self._char_pause_duration)

                if is_vk_event:
                    self._push_vk_event(vk, up)
                else:
                    self._push_utf16_code_unit_event(code_unit, up)

            if i % 200 == 0 and time.perf_counter() - self._start_time >= _INSERTION_TIMEOUT:
                raise RuntimeError("Text insertion took too long.")

            had_surrogate = is_surrogate

        must_wait = self._must_wait_for_stable_caret and self._num_events > 1  # More than single up-event.
        self._flush()
        if must_wait:
            self._wait_for_stable_caret()

    def _push_vk_event(self, vk: int, up: bool):
        scancode = win32api.MapVirtualKey(vk, MAPVK_VK_TO_VSC_EX)
        has_e0_extended_scan_code = (scancode & 0xFF00) == 0xE000
        #i AI GPT-5.2 thinks `wScan` must not contain the extended-prefix. But the docs for `KEYEVENTF_EXTENDEDKEY` seem to say otherwise.
        #i
        #i We just ignore 0 on missing translation, because we don't use `KEYEVENTF_SCANCODE`, but primarily rely on the virtual-key code. The scancode is just for maximizing compatibility.

        event = self._events[self._num_events]

        event.type = win32con.INPUT_KEYBOARD
        event.ki.wVk = vk
        event.ki.wScan = scancode

        flags = 0
        if has_e0_extended_scan_code:
            flags |= win32con.KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= win32con.KEYEVENTF_KEYUP
        event.ki.dwFlags = flags

        event.ki.time = 0
        event.ki.dwExtraInfo = 0

        self._num_events += 1
    
    def _push_utf16_code_unit_event(self, code_unit: int, up: bool):
        event = self._events[self._num_events]

        event.type = win32con.INPUT_KEYBOARD
        event.ki.wVk = 0
        event.ki.wScan = code_unit

        flags = win32con.KEYEVENTF_UNICODE
        if up:
            flags |= win32con.KEYEVENTF_KEYUP
        event.ki.dwFlags = flags

        event.ki.time = 0
        event.ki.dwExtraInfo = 0

        self._num_events += 1

    def _flush(self):
        if self._num_events <= 0:
            return

        # Check for various obstacles.
        if self._abort_on_window_xor_app_change:
            active_window = ui.active_window()
            if active_window.id != self._insertion_hwnd:
                raise RuntimeError(f"Active window changed during text insertion. Insertion aborted. Displacing window and app: `{active_window}` (HWND: {hex(active_window.id)}).")
        else:
            active_window = ui.active_window()
            if active_window.app.pid != self._insertion_pid:
                raise RuntimeError(f"Active app changed during text insertion. Insertion aborted. Displacing window and app: `{active_window}` (HWND: {hex(active_window.id)}).")

        self._get_gui_thread_info()
        if self._gui_thread_info.flags & (win32con.GUI_SYSTEMMENUMODE | win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE):
            raise RuntimeError("Menu active. Text insertion aborted.")
        #i This technique only works for traditional Win32 menus incl. a window's system menu. The universal way would be to check whether `talon.windows.ax.get_focused_element().control_type` is `"MenuBar"` or `"MenuItem"`. But unfortunately, this API is very slow and would introduce a delay of up to about 83 ms according to the author's measurements before every flush.

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
            self._num_events, self._events, ctypes.sizeof(INPUT)
        )
        if num_events_sent == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if num_events_sent != self._num_events:
            raise RuntimeError("Failed to send all keyboard input requests.")

        # Reset event queue with regard to next flush.
        self._num_events = 0

    def _wait_for_stable_caret(self):
        self._get_gui_thread_info()

        if not (self._gui_thread_info.flags & win32con.GUI_CARETBLINKING):
            # Change insertion mode. Previous mass event enqueuing may cause problems that may be unavoidable at this point.
            self._must_wait_for_stable_caret = False
            return

        # Try to ensure window reacts quickly enough. (It may not on very high CPU load, e.g.)
        UNREACTIVE_TIMEOUT_MS = 200

        hwnd = self._gui_thread_info.hwndFocus or self._gui_thread_info.hwndActive
        if not hwnd:
            raise RuntimeError("Couldn't determine window.")

        try:
            win32gui.SendMessageTimeout(
                hwnd,
                win32con.WM_NULL,
                0,
                0,
                win32con.SMTO_BLOCK | SMTO_ERRORONEXIT,
                UNREACTIVE_TIMEOUT_MS,
            )
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_TIMEOUT:
                raise RuntimeError("Text insertion window was to slow to react.")
            else:
                raise

        # Wait for caret.
        num_empty_rects = 0

        last_rect = wintypes.RECT()
        last_rect_fields = memoryview(last_rect).cast("B").cast("l")

        last_move_time = time.perf_counter()

        while True:
            self._get_gui_thread_info()
            rect = self._gui_thread_info.rcCaret
            rect_fields = memoryview(rect).cast("B").cast("l")
            #i The `GetGUIThreadInfo()` docs' remarks talk about strange encoding of special values in `rcCaret`. But since we just wait for the values to stabilize, we assume that this doesn't matter.

            if last_rect is None:
                # Fetch first rect to get going.
                pass
            elif not any(rect_fields):
                # Tolerate only a couple of empty rects that sporadically may be returned.
                num_empty_rects += 1
                if num_empty_rects >= 5:
                    # Give up and change insertion mode.
                    self._must_wait_for_stable_caret = False
                    break
            else:
                now = time.perf_counter()
                if rect_fields != last_rect_fields:
                    # Reset.
                    last_move_time = now
                elif now - last_move_time >= self._stable_caret_duration:
                    # Assume caret as stable.
                    break

                if now - self._start_time >= _INSERTION_TIMEOUT:
                    raise RuntimeError("Text insertion took too long while waiting for caret to stop moving.")

            actions.sleep(0.001)  # Throttle.
            last_rect_fields[:] = rect_fields

    def _get_gui_thread_info(self):
        success = user32.GetGUIThreadInfo(0, ctypes.byref(self._gui_thread_info))
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())


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
