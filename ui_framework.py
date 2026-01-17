import ctypes
import re
from typing import Optional, TYPE_CHECKING

from talon import Module, app, ui
from talon.ui import Window
from talon.windows import ax

if app.platform == "windows" or TYPE_CHECKING:
    import pywintypes
    import win32api
    import win32con
    import win32gui
    import win32process
    import winerror

    from .winapi import GUITHREADINFO, LIST_MODULES_ALL, MAPVK_VK_TO_VSC_EX, kernel32, user32

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

_ui_framework = None

def _script_main():
    ui.register("win_focus", _on_win_focus)

@_mod.scope
def _ui_framework_scope():
    return {"ui_framework": _ui_framework or "unknown"}

def _on_win_focus(window):
    global _ui_framework
    _ui_framework = _detect_ui_framework(window)
    _on_win_focus_after_detection()
    _ui_framework_scope.update()

def _on_win_focus_after_detection():
    global _ui_framework

    if _ui_framework == "Qt":
        #i When a Qt window is activated, its caret may first not be reported by `GetGUIThreadInfo()` anymore, which would be very useful for the `insert()` override. As it turns out, just briefly pressing the Shift key makes the caret be reported again. When trying to automate this, an attempt to send Shift via `SendInput()` didn't work. With `SendMessage()`, it worked. But sending `VK_NONAME` is even more innocuous and also works. (Tested in output pane of gImageReader v3.4.3.)

        gui_thread_info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        success = user32.GetGUIThreadInfo(0, ctypes.byref(gui_thread_info))
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

        hwnd = gui_thread_info.hwndFocus or gui_thread_info.hwndActive
        if not hwnd:
            raise RuntimeError("Couldn't determine window.")

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

def _detect_ui_framework(window: Window) -> Optional[str]:
    """Detects the specified top-level window's UI framework, specifically with regard to event loop matters and automating the window.

    Subparts of a window may still be handled by other frameworks, which this function doesn't cover. E.g., data display controls in the Windows 11 Task Manager or the file display in the OS's open- and save-dialogs are implemented by DirectUI, and SWT uses Win32 controls.
    """

    #i Useful tools for investigation:
    #i - Window Spy for AHKv2 (comes with AutoHotkey; <https://www.autohotkey.com/>)
    #i - WinSpy++ (<https://www.catch22.net/projects/winspy/>)
    #i - Spy++ (comes with Visual Studio; <https://learn.microsoft.com/en-us/visualstudio/debugger/introducing-spy-increment>)
    #i - Accessibility Insights for Windows (<https://accessibilityinsights.io/>)
    #i - System Informer (<https://systeminformer.sourceforge.io/>)
    #i - Detect It Easy (<https://horsicq.github.io/#detect-it-easydie>)

    is_gdk = False
    maybe_wpf = False

    # Check Win32 class name of top-level window.
    toplevel_hwnd = window.id
    toplevel_class = window.cls

    while True:
        match toplevel_class:
            case "#32770":  # Dialog system class.
                #TODO: This regularly detects default open- and save-dialogs incorrectly (e.g., in Balabolka). Maybe check child tree for hints.
                # Try again with owner window, if available.
                try:
                    toplevel_hwnd = win32gui.GetWindow(toplevel_hwnd, win32con.GW_OWNER)
                    if toplevel_hwnd:
                        toplevel_class = win32gui.GetClassName(toplevel_hwnd)
                        continue
                except pywintypes.error as e:
                    if e.winerror != winerror.ERROR_INVALID_WINDOW_HANDLE:
                        print("WARNING: Exception while trying to get owner window during UI framework detection:", e)
                    return None
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
                is_gdk = True
                #i GIMP Drawing Kit.
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
                    maybe_wpf = True
                    #i Windows Presentation Foundation (.NET).
                    #i Apps: Visual Studio Installer, Visual Studio, Accessibility Insights for Windows, ILSpy.

        break

    # Check UI Automation data.
    if maybe_wpf:
        try:
            element = ax.get_element_from_handle(window.id)
            framework_id = element.framework_id
        except Exception as e:
            print("WARNING: UI Automation API error during UI framework detection:", e)
            return None

        #i - `"Win32"` as the framework ID is often only a placeholder, because no better value is provided.
        #i - It may be that a framework ID other than `"Win32"` is only provided on the child level. (This is, e.g., the case with VS Code.) But care must be taken not to confuse the framework ID of a control-level child (not covering the whole window surface) with the entire window's framework ID (see also other comment talking about DirectUI).
        #i - `element.class_name` may also contain useful information. There may be no corresponding Win32 window for the element, but because this is a different concept than the Win32 class name, the UIA class name can still be available.
        #i - `element.automation_id` may also contain useful information.

        if framework_id == "WPF":
            return "WPF"

    # Check Win32 class names of direct Win32 child windows.
    if not is_gdk:
        try:
            hwnd = win32gui.GetWindow(window.id, win32con.GW_CHILD)
            while hwnd:
                child_class = win32gui.GetClassName(hwnd)

                if _mfc_class_regex.search(child_class):
                    return "MFC"
                    #i Microsoft Foundation Classes (C++).
                    #i Apps: MPC-HC.
                elif _winui_limited_class_regex.search(child_class):
                    return "WinUI"
                    #i Microsoft apps shipped with Windows, either hosted by `ApplicationFrameHost.exe` (Media Player etc.) or not (Notepad, Paint).

                hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
        except pywintypes.error as e:
            if e.winerror != winerror.ERROR_INVALID_WINDOW_HANDLE:
                print("WARNING: Exception while walking through child windows for UI framework detection:", e)
            return None
            
    # Check DLL filenames.
    if is_gdk:
        try:
            process_handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                window.app.pid,
            )

            try:
                module_handles = win32process.EnumProcessModulesEx(
                    process_handle,
                    LIST_MODULES_ALL,
                )

                filename_buffer = ctypes.create_unicode_buffer(256)
                #i Maximum path *component* length as per <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>.

                for module_handle in module_handles:
                    success = kernel32.K32GetModuleBaseNameW(process_handle.handle, module_handle, filename_buffer, len(filename_buffer))
                    if not success:
                        raise ctypes.WinError(ctypes.get_last_error())
                    filename = filename_buffer.value

                    if is_gdk:
                        if _gtk_dll_regex.search(filename):
                            return "GTK"
                            #i Originally "GIMP ToolKit".
                            #i Apps: See "GDK".
            finally:
                process_handle.Close()
        except pywintypes.error as e:
            print(f"WARNING: Exception while trying to retrieve loaded modules of process {window.app.pid}:", e)
            return None

    #
    return None

_script_main()
