import ctypes
import struct
from typing import TYPE_CHECKING

from talon import Context, Module, actions, app, settings, ui

if app.platform == "windows" or TYPE_CHECKING:
    import win32api
    import win32con

    from .winapi import INPUT, GUITHREADINFO, MAPVK_VK_TO_VSC_EX, user32

mod = Module()

mod.setting(
    "key_hold_applies_to_chars",
    type=bool,
    default=False,
    desc="Whether the `key_hold` setting applies to characters directly sent as themselves by the `insert()` override (as opposed to their respective keys). This is necessary in certain apps - specifically those based on the Qt framework - because they otherwise may drag non-BMP characters (> U+FFFF) to an earlier position when the app didn't have enough time to process the characters before the non-BMP characters.",
)

ctx = Context()

VKS_OF_ASCII_CODES = {
    # Keyboard-layout-invariant virtual-key codes that don't work with the `KEYEVENTF_UNICODE` flag.
    0x08: win32con.VK_BACK,
    0x09: win32con.VK_TAB,
    0x0A: win32con.VK_RETURN,
    0x1B: win32con.VK_ESCAPE,
}


@ctx.action_class("main")
class MainActions:
    def insert(text: str):
        """An override of Talon's original `insert()` function that won't cause problems with dead keys and can be much faster.

        Characters are sent to the active window as themselves and not as their respective key presses, the latter of which are keyboard-layout-dependent. The only real key events simulated are for these keys:

        - Backspace (`\b`, `\N{BS}`, `\N{BACKSPACE}`)
        - Tab (`\t`, `\N{TAB}`)
        - Enter (`\n`, `\N{NEW LINE}`)
        - Esc (`\x1b`, `\N{ESC}`, `\N{ESCAPE}`; can be used before Enter or Tab to prevent confirming a suggestion)

        Talon's setting `key_hold` still applies. Use the setting `user.key_hold_applies_to_chars` to optimize for speed or compatibility.

        The function also aborts insertion when the active window changes, when a traditional OS menu is active, and when a modifier key is held down. This is especially effective in slow mode, i.e., when the setting is `true`.
        """

        key_hold_duration = settings.get("key_hold") / 1000
        hold_key_unconditionally = settings.get("user.key_hold_applies_to_chars")
        #i `key_wait` only applies when modifiers are involved, which isn't the case here.

        utf16le_bytes = text.encode("utf-16-le", errors="surrogatepass")
        code_units = memoryview(utf16le_bytes).cast("@H")

        events = (
            INPUT * (2 if hold_key_unconditionally else (len(code_units) * 2))
            #i Down and up for every code unit.
        )()
        num_events_to_send = 0

        insertion_window_id = ui.active_window().id
        gui_thread_info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))

        def flush():
            nonlocal num_events_to_send, gui_thread_info

            if num_events_to_send > 0:
                # Check for window change.
                if ui.active_window().id != insertion_window_id:
                    raise RuntimeError(
                        "Active window changed during text insertion. Insertion aborted."
                    )

                # Check for active menu.
                success = user32.GetGUIThreadInfo(0, ctypes.byref(gui_thread_info))
                if not success:
                    raise ctypes.WinError(ctypes.get_last_error())
                if gui_thread_info.flags & win32con.GUI_INMENUMODE:
                    raise RuntimeError("Menu active. Text insertion aborted.")
                #i This technique only works for traditional Win32 menus incl. a window's system menu. The universal way would be to check whether `talon.windows.ax.get_focused_element().control_type` is `"MenuBar"` or `"MenuItem"`. But unfortunately, this API is very slow and would introduce a delay of up to about 83 ms according to the author's measurements before every flush.

                # Check for held-down modifier keys.
                if (
                    win32api.GetAsyncKeyState(win32con.VK_CONTROL) < 0
                    or win32api.GetAsyncKeyState(win32con.VK_SHIFT) < 0
                    or win32api.GetAsyncKeyState(win32con.VK_MENU) < 0  # Alt.
                    or win32api.GetAsyncKeyState(win32con.VK_LWIN) < 0
                    or win32api.GetAsyncKeyState(win32con.VK_RWIN) < 0
                ):
                    raise RuntimeError(
                        "Modifier key held down. Text insertion aborted."
                    )

                # Send prepared events.
                num_events_sent = user32.SendInput(
                    num_events_to_send, events, ctypes.sizeof(INPUT)
                )
                if num_events_sent == 0:
                    raise ctypes.WinError(ctypes.get_last_error())
                if num_events_sent != num_events_to_send:
                    raise RuntimeError("Failed to send all keyboard input requests.")

                # Reset event array with regard to next flush.
                num_events_to_send = 0

        for code_unit in code_units:
            vk = VKS_OF_ASCII_CODES.get(code_unit, 0)
            is_vk_event = bool(vk)

            if code_unit < 0x20 and not is_vk_event:
                continue

            # is_surrogate = code_unit >= 0xD800 and code_unit <= 0xDFFF

            if is_vk_event:
                scancode = win32api.MapVirtualKey(vk, MAPVK_VK_TO_VSC_EX)
                has_e0_extended_scan_code = (scancode & 0xFF00) == 0xE000
                scancode_or_code_unit = scancode
                #i AI GPT-5.2 thinks `wScan` must not contain the extended-prefix. But the docs for `KEYEVENTF_EXTENDEDKEY` seem to say otherwise.
                #i
                #i We just ignore 0 on missing translation, because we don't use `KEYEVENTF_SCANCODE`, but primarily rely on the virtual-key code. The scancode is just for maximizing compatibility.
            else:
                has_e0_extended_scan_code = False
                scancode_or_code_unit = code_unit

            for is_down_event in [True, False]:
                if not is_down_event and (hold_key_unconditionally or is_vk_event):
                    flush()
                    actions.sleep(key_hold_duration)

                event = events[num_events_to_send]

                event.type = win32con.INPUT_KEYBOARD
                event.ki.wVk = vk
                event.ki.wScan = scancode_or_code_unit

                flags = 0
                if has_e0_extended_scan_code:
                    flags |= win32con.KEYEVENTF_EXTENDEDKEY
                if not is_vk_event:
                    flags |= win32con.KEYEVENTF_UNICODE
                if not is_down_event:
                    flags |= win32con.KEYEVENTF_KEYUP
                event.ki.dwFlags = flags

                event.ki.time = 0
                event.ki.dwExtraInfo = 0

                num_events_to_send += 1

        flush()
