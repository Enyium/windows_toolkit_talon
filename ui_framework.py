"""
This file makes the `user.ui_framework` Talon scope available that can be used for window matching. The assessments are cached in the windows themselves using window properties. Whenever Talon reloads the file, the assessments are reset (without removing the window properties).

The file also prepares some frameworks' windows for automation in an indiscernible manner whenever they're activated.
"""

from collections import deque
import ctypes
from enum import Enum, auto
import re
import textwrap
import time
import traceback
from typing import Optional, TYPE_CHECKING

from talon import Module, app, cron, ui
from talon.ui import Window

if app.platform == "windows" or TYPE_CHECKING:
    import pywintypes
    import win32api
    import win32con
    import win32gui
    import win32process
    import winerror

    from talon.windows import ax

    from .winapi import GUITHREADINFO, LIST_MODULES_ALL, MAPVK_VK_TO_VSC_EX, kernel32, user32
else:
    raise NotImplementedError("Unsupported OS.")

_mod = Module()

#. Win32 class name regexes.
_mfc_class_regex = re.compile(r"^Afx[:A-Z]")
_qt_class_regex = re.compile(r"^(?:Qt\d+QWindowIcon|QWidget)$")
_visual_component_library_class_regex = re.compile(r"""(?x)
    ^(?:
        (?: TForm | Tfm | (?:[A-Z]{2})?Tfrm) (?: [A-Z][a-z]+ | [A-Z]+ )+
        | T (?: (?: [A-Z][a-z]+ | [A-Z]+ ) _? )+ Form
    )$
""")
_winforms_class_regex = re.compile(r"^WindowsForms\d+\.")
_winui_limited_class_regex = re.compile(r"^(?:Windows|Microsoft)\.UI\.")

_gtk_dll_regex = re.compile(r"(?i)^libgtk-[\d.-]+\.dll$")

_framework = None  # Last assessment for communication with Talon scope.

_retry_job = None  # Not `None` means pending assessment.
_retry_start = 0
_retry_window = None

def _script_main():
    ui.register("win_focus", _on_win_focus)

def _on_win_focus(toplevel_window: Window):
    _abort_retry()
    _update_scope(toplevel_window)

def _retry_if_not_timed_out(window: Window):
    global _retry_job, _retry_start, _retry_window

    if _retry_job:
        TIMEOUT = 2.0
        if time.perf_counter() - _retry_start >= TIMEOUT:
            _retry_job = None

            # Cause special error value for framework.
            raise RuntimeError("Timeout reached while trying to recognize UI framework.")
    else:  # Just starting out.
        _retry_start = time.perf_counter()

    _retry_window = window
    _retry_job = cron.after("100ms", _on_retry_job)

def _abort_retry():
    global _retry_job, _retry_window
    cron.cancel(_retry_job)
    _retry_job = None
    _retry_window = None

def _on_retry_job():
    global _retry_window
    _update_scope(_retry_window)

def _update_scope(toplevel_window: Window):
    global _retry_job, _framework

    try:
        _framework = _Detector()(toplevel_window)
        if _framework:
            _prepare_active_window(_framework)
    except Exception:
        # Convert exception into mere output, so the Talon scope can't keep delivering a past framework, which could lead to erratic input behavior. (Actual Talon scope behavior not tested.)
        print(
            "ERROR: Exception during UI framework detection:\n"
            + textwrap.indent(
                traceback.format_exc()
                + f"Active top-level window (ID {hex(toplevel_window.id)}): {toplevel_window}",
                "  ",
            )
        )

        _framework = "error"
        #i This special value allows for slow input fallbacks.

    _ui_framework_scope.update()

@_mod.scope
def _ui_framework_scope():
    return {
        "ui_framework": _framework or ("pending" if _retry_job else "unknown"),
    }


class _Detector:
    """Calling an instance detects the specified top-level window's UI framework, specifically with regard to event loop matters and automating the window.

    Parts of a window may still be handled by other frameworks, which this class doesn't cover: E.g., data display controls in the Windows 11 Task Manager, or the file display in the OS's open- and save-dialogs are implemented by DirectUI, and SWT uses Win32 controls.
    """

    #i Useful tools for investigation:
    #i - Window Spy for AHKv2 (comes with AutoHotkey; <https://www.autohotkey.com/>)
    #i - WinSpy++ (<https://www.catch22.net/projects/winspy/>)
    #i - Spy++ (comes with Visual Studio; <https://learn.microsoft.com/en-us/visualstudio/debugger/introducing-spy-increment>)
    #i - Accessibility Insights for Windows (<https://accessibilityinsights.io/>)
    #i - System Informer (<https://systeminformer.sourceforge.io/>)
    #i - Detect It Easy (<https://horsicq.github.io/#detect-it-easydie>)

    def __call__(self, toplevel_window: Window) -> Optional[str]:
        return self._check_toplevel_window_and_its_cache(toplevel_window)

    def _check_toplevel_window_and_its_cache(self, toplevel_window: Window) -> Optional[str]:
        """Reads the top-level window's UI framework from its window properties, or tries to recognize it."""

        #TODO: Read and write window properties to cache the assessment.
        return self._check_toplevel_window(toplevel_window)

    def _check_toplevel_window(self, toplevel_window: Window) -> Optional[str]:
        """Tries to recognize the top-level window's UI framework."""

        class ExtraSource(Enum):
            CHILD_WINDOW_TREE = auto()
            MODULE_FILENAMES = auto()
            UIA_DATA = auto()

        toplevel_class = toplevel_window.cls  # Win32 class name.
        extra_sources = deque((ExtraSource.CHILD_WINDOW_TREE,))

        def remove_extra_source(source: ExtraSource):
            nonlocal extra_sources
            try:
                extra_sources.remove(source)
            except ValueError:
                pass

        def favor_extra_source(source: ExtraSource):
            nonlocal extra_sources
            remove_extra_source(source)
            extra_sources.appendleft(source)

        match toplevel_class:
            case "#32770":  # Dialog system class.
                # Try again with owner window, if available.
                owner_window = _get_owner_window(toplevel_window)
                #TODO: This regularly detects default open- and save-dialogs incorrectly (e.g., in Balabolka). Maybe check child tree for hints - before owner detection on same PIDs, and after this `case` on different PIDs.
                if owner_window and owner_window.app.pid == toplevel_window.app.pid:
                    framework = self._check_toplevel_window_and_its_cache(owner_window)
                    if framework:
                        return framework
            case "AutoHotkeyGUI":
                return "AutoHotkey"
                #i Apps: Window Spy for AHKv2.
            case "SunAwtFrame" | "SunAwtDialog":
                return "AWT/Swing"
                #i Abstract Window Toolkit (Java).
                #i Apps: Android Studio, Swing App Example, ImageJ, SINE Isochronic Entrainer.
            case "ThunderRT6FormDC":
                return "classic Visual Basic"
                #i Apps: CharProbe (<https://web.archive.org/web/20130312122416/http://www.dextronet.com/charprobe>), Color Selector (<https://colorselector.sourceforge.net/>).
            case "FLUTTER_RUNNER_WIN32_WINDOW":
                return "Flutter"
            case "gdkWindowToplevel" | "gdkSurfaceToplevel":
                return self._check_module_filenames(toplevel_window.app.pid, {"GTK"})
                #i GDK: GIMP Drawing Kit.
                #i Apps: Inkscape, Qalculate, Czkawka.
            case "MozillaWindowClass":
                return "Gecko"
                #i Also reported as such by UI Automation API.
                #i Apps: Firefox, Firefox derivates, Thunderbird, Zotero.
            case "SWT_Window0" | "SWT_WindowShadow0":
                return "SWT"
                #i Standard Widget Toolkit (Java).
                #i Apps: Eclipse IDE.
            case "SALFRAME" | "SALSUBFRAME":
                return "Visual Class Library"
                #i (C++) Not to be confused with "Visual Component Library".
                #i Apps: LibreOffice, Apache OpenOffice.
            case "WinUIDesktopWin32WindowClass":
                return "WinUI"
                #i Apps: Microsoft PowerToys.
            case "wxWindowNR":
                return "wxWidgets"
                #i Apps: Tenacity, HTerm.
            case _:
                if toplevel_class.startswith("ATL:"):
                    return "ATL"
                    #i Active Template Library (C++).
                    #i Apps: Autoruns.
                elif toplevel_class.startswith("Chrome_WidgetWin_"):
                    return "Chrome"
                    #i Also reported as such by UI Automation API.
                    #i Apps: Chrome, Chromium derivates, Electron apps.
                elif toplevel_class.startswith("GlassWndClass-GlassWindowClass-"):
                    return "JavaFX"
                    #i Apps: AsciidocFX, PDFsam Basic.
                elif _mfc_class_regex.search(toplevel_class):
                    return "MFC"
                    #i Microsoft Foundation Classes (C++).
                    #i Apps: NVIDIA Control Panel, O&O RegEditor, PDFill PDF Tools.
                elif _qt_class_regex.search(toplevel_class):
                    return "Qt"
                    #i Apps: Equalizer APO, SQLiteStudio, XnConvert.
                elif _visual_component_library_class_regex.search(toplevel_class):
                    return "Visual Component Library"
                    #i (mainly Delphi) Not to be confused with "Visual Class Library".
                    #i Apps: Balabolka, HxD, Billy (<https://github.com/zQueal/Billy>), HDDScan.
                elif _winforms_class_regex.search(toplevel_class):
                    return "WinForms"
                    #i Windows Forms (.NET).
                    #i Apps: Shutdown Timer Classic, AS SSD Benchmark.
                elif _winui_limited_class_regex.search(toplevel_class):
                    return "WinUI"
                    #i Windows taskbar's start and search flyouts.
                elif toplevel_class.startswith("HwndWrapper["):
                    # Probably WPF.
                    favor_extra_source(ExtraSource.UIA_DATA)
                    #i Windows Presentation Foundation (.NET).
                    #i Apps: Visual Studio Installer, Visual Studio, Accessibility Insights for Windows, ILSpy.

        framework = None
        for source in extra_sources:
            match source:
                case ExtraSource.CHILD_WINDOW_TREE:
                    framework = self._check_child_window_tree(toplevel_window)
                case ExtraSource.MODULE_FILENAMES:
                    framework = self._check_module_filenames(toplevel_window.app.pid)
                case ExtraSource.UIA_DATA:
                    framework = self._check_uia_data(toplevel_window)

            if framework:
                return framework

        if toplevel_class == "ApplicationFrameWindow":
        #i Probably hosted WinUI app. (Process A has child windows of process B.)
            # Try again for some duration until app hopefully loaded in a recognizable manner.
            _retry_if_not_timed_out(toplevel_window)

        return None

    def _check_child_window_tree(self, toplevel_window: Window, possible_frameworks: Optional[set[str]] = None) -> Optional[str]:
        """Tries to recognize the top-level window's UI framework by its Win32 child window tree."""

        wants_mfc = not possible_frameworks or "MFC" in possible_frameworks
        wants_winui = not possible_frameworks or "WinUI" in possible_frameworks

        framework = None

        def handle_child_window(hwnd, _):
            nonlocal framework

            try:
                child_class = win32gui.GetClassName(hwnd)
            except pywintypes.error as e:
                if e.winerror == winerror.ERROR_INVALID_WINDOW_HANDLE:
                    return True  # Continue.
                else:
                    raise

            #i The order of the checks can be relevant. See also other comment regarding DirectUI.
            if wants_mfc and _mfc_class_regex.search(child_class):
                framework = "MFC"
                #i Microsoft Foundation Classes (C++).
                #i Apps: MPC-HC.
                return False
            if wants_winui and _winui_limited_class_regex.search(child_class):
                # global _retry_job, _retry_start
                # if _retry_job:
                #     print(f"Duration until recognition: {(time.perf_counter() - _retry_start) * 1000:.0f} ms")

                framework = "WinUI"
                #i Microsoft apps shipped with Windows, either hosted by `ApplicationFrameHost.exe` (Clock, Feedback Hub, Media Player) or not (Notepad, Paint).
                return False

            return True

        win32gui.EnumChildWindows(toplevel_window.id, handle_child_window, None)
        return framework

    def _check_module_filenames(self, pid, possible_frameworks: Optional[set[str]] = None) -> Optional[str]:
        """Tries to recognize the process's UI framework by its module filenames (mostly DLLs)."""

        wants_gtk = not possible_frameworks or "GTK" in possible_frameworks

        process_handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)

        try:
            module_handles = win32process.EnumProcessModulesEx(process_handle, LIST_MODULES_ALL)

            filename_buffer = ctypes.create_unicode_buffer(256)
            #i Maximum path *component* length as per <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>.

            for module_handle in module_handles:
                success = kernel32.K32GetModuleBaseNameW(process_handle.handle, module_handle, filename_buffer, len(filename_buffer))
                if not success:
                    raise ctypes.WinError(ctypes.get_last_error())
                filename = filename_buffer.value

                if wants_gtk and _gtk_dll_regex.search(filename):
                    return "GTK"
                    #i Originally "GIMP ToolKit".
                    #i Apps: See "GDK".
        finally:
            process_handle.Close()

    def _check_uia_data(self, toplevel_window: Window, possible_frameworks: Optional[set[str]] = None) -> Optional[str]:
        """Tries to recognize the top-level window's UI framework by its UI Automation data."""

        wants_wpf = not possible_frameworks or "WPF" in possible_frameworks

        element = ax.get_element_from_handle(toplevel_window.id)
        framework_id = element.framework_id

        #i - `"Win32"` as the framework ID is often only a placeholder, because no better value is provided.
        #i - It may be that a framework ID other than `"Win32"` is only provided on the child level. (This is, e.g., the case with VS Code.) But care must be taken not to confuse the framework ID of a control-level child (not covering the whole window surface) with the entire window's framework ID (see also other comment talking about DirectUI).
        #i - `element.class_name` may also contain useful information. There may be no corresponding Win32 window for the element, but because this is a different concept than the Win32 class name, the UIA class name can still be available.
        #i - `element.automation_id` may also contain useful information.

        if wants_wpf and framework_id == "WPF":
            return "WPF"

        return None


def _get_owner_window(window: Window) -> Optional[Window]:
    try:
        owner_hwnd = win32gui.GetWindow(window.id, win32con.GW_OWNER)
    except pywintypes.error as e:
        if e.winerror == winerror.ERROR_INVALID_WINDOW_HANDLE:
            return None
        else:
            raise

    windows = ui.windows(id=owner_hwnd)  # `NULL` simply yields nothing.
    return windows[0] if windows else None

def _prepare_active_window(framework: Optional[str]):
    if framework == "Qt":
        #i When a Qt window is activated, its caret may first not be reported by `GetGUIThreadInfo()` anymore, which would be very useful for the `insert()` override. As it turns out, just briefly pressing the Shift key makes the caret be reported again. When trying to automate this, an attempt to send Shift via `SendInput()` didn't work. With `SendMessage()`, it worked. But sending `VK_NONAME` is even more innocuous and also works. (Tested in output pane of gImageReader v3.4.3.)

        gui_thread_info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        success = user32.GetGUIThreadInfo(0, ctypes.byref(gui_thread_info))
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

        hwnd = gui_thread_info.hwndFocus or gui_thread_info.hwndActive
        if not hwnd:
            raise RuntimeError("Couldn't determine window for preparation after UI framework detection.")

        vk = win32con.VK_NONAME
        scancode = win32api.MapVirtualKey(vk, MAPVK_VK_TO_VSC_EX)
        is_extended_scancode = bool(scancode & 0xFF00)

        shared_lparam = (
            1  # Repeat count.
            | ((scancode & 0xFF) << 16)
            | (int(is_extended_scancode) << 24)
        )
        win32gui.SendMessage(hwnd, win32con.WM_KEYDOWN, vk, shared_lparam)
        win32gui.SendMessage(hwnd, win32con.WM_KEYUP, vk, shared_lparam | (1 << 30) | (1 << 31))

_script_main()
