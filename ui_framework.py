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
from typing import Callable, Optional, TYPE_CHECKING

from talon import Module, app, cron, ui
from talon.ui import Window

from .lib.str_carrying_one_based_int_enum import StrCarryingOneBasedIntEnum

if app.platform == "windows" or TYPE_CHECKING:
    import pywintypes
    import win32api
    import win32con
    import win32gui
    import win32process
    import winerror

    from talon.windows import ax

    from .lib.winapi import GUITHREADINFO, LIST_MODULES_ALL, MAPVK_VK_TO_VSC_EX, kernel32, user32
else:
    raise NotImplementedError("Unsupported OS.")

_MUST_CACHE_ASSESSMENT = True
"""Can be turned off for debug purposes."""

_script_load_time_ns = time.perf_counter_ns()
_mod = Module()


class UIFramework(StrCarryingOneBasedIntEnum):
    # Special variants.
    UNKNOWN = "unknown"
    """No specific framework could be detected. Probably something like bare Win32, or a custom-drawing framework that doesn't leave any hints about it."""

    PENDING = "pending"
    """The framework couldn't be detected right away on window activation, so a number of retries are undertaken until a timeout occurs."""

    ERROR = "error"
    """The detection code produced an exception that was logged in Talon's log. Allows for slow-input fallbacks."""

    # Concrete UI frameworks.
    @property
    def is_concrete(self) -> bool:
        return self > UIFramework.ERROR

    ATL = "ATL"
    """- Active Template Library (C++)
    - Apps: Autoruns"""

    AUTO_HOTKEY = "AutoHotkey"
    """Apps: Window Spy for AHKv2"""

    AWT = "AWT"
    """- Abstract Window Toolkit, typically in combination with Swing (Java)
    - Apps: Android Studio, Swing App Example, ImageJ, SINE Isochronic Entrainer"""

    CHROME = "Chrome"
    """- Also reported as such by UI Automation API
    - Apps: Chrome, Chromium derivates, Electron apps"""

    CLASSIC_VISUAL_BASIC = "classic Visual Basic"
    """Apps: [CharProbe](https://web.archive.org/web/20130312122416/http://www.dextronet.com/charprobe), [Color Selector](https://colorselector.sourceforge.net)"""

    FLUTTER = "Flutter"

    GECKO = "Gecko"
    """- Also reported as such by UI Automation API
    - Apps: Firefox, Firefox derivates, Thunderbird, Zotero"""

    GTK = "GTK"
    """- Originally "GIMP Toolkit"
    - Apps: Inkscape, Qalculate (one variant), Czkawka"""

    JAVA_FX = "JavaFX"
    """Apps: AsciidocFX, PDFsam Basic"""

    MFC = "MFC"
    """- Microsoft Foundation Classes (C++)
    - Apps:
      - NVIDIA Control Panel, O&O RegEditor, PDFill PDF Tools
      - MPC-HC"""

    QT = "Qt"
    """Apps: Equalizer APO, SQLiteStudio, XnConvert"""

    SWT = "SWT"
    """- Standard Widget Toolkit (Java)
    - Apps: Eclipse IDE"""

    VISUAL_CLASS_LIBRARY = "Visual Class Library"
    """- (C++)
    - Not to be confused with "Visual Component Library"
    - Apps: LibreOffice, Apache OpenOffice"""

    VISUAL_COMPONENT_LIBRARY = "Visual Component Library"
    """- (mainly Delphi)
    - Not to be confused with "Visual Class Library"
    - Apps: Balabolka, HxD, [Billy](https://github.com/zQueal/Billy), HDDScan"""

    WIN_FORMS = "WinForms"
    """- Windows Forms (.NET)
    - Apps: Shutdown Timer Classic, AS SSD Benchmark"""

    WINRT_XAML = "WinRT XAML"
    """Apps:
    - UWP XAML:
      - Windows taskbar's start and search flyouts
      - Microsoft apps shipped with Windows, hosted by `ApplicationFrameHost.exe`, like Clock, Feedback Hub, Media Player
    - XAML Islands:
      - Windows Alt+Tab task switcher
    - WinUI 3:
      - Microsoft PowerToys
      - Microsoft apps shipped with Windows, not hosted by `ApplicationFrameHost.exe`, like Notepad, Paint"""

    WPF = "WPF"
    """- Windows Presentation Foundation (.NET)
    - Apps: Visual Studio Installer, Visual Studio, Accessibility Insights for Windows, ILSpy"""

    WX_WIDGETS = "wxWidgets"
    """Apps: Tenacity, HTerm"""


#. Win32 class names.
_mfc_class_regex = re.compile(r"^Afx[:A-Z]")
_qt_class_regex = re.compile(r"^(?:Qt\d+QWindowIcon|QWidget)$")
_visual_component_library_class_regex = re.compile(r"""(?x)
    ^(?:
        (?: TForm | Tfm | (?:[A-Z]{2})?Tfrm) (?: [A-Z][a-z]+ | [A-Z]+ )+
        | T (?: (?: [A-Z][a-z]+ | [A-Z]+ ) _? )+ Form
    )$
""")
_winforms_class_regex = re.compile(r"^WindowsForms\d+\.")
_WINRT_XAML_CHILD_CLASSES = frozenset({
    "Windows.UI.Core.CoreWindow",
    #i See also <https://learn.microsoft.com/en-us/uwp/api/windows.ui.core.corewindow>.
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Microsoft.UI.Content.DesktopChildSiteBridge",
})

_gtk_dll_regex = re.compile(r"(?i)^libgtk-[\d.-]+\.dll$")

_framework = UIFramework.PENDING
"""Last assessment for communication with Talon scope."""

_retry_job = None
_retry_start = 0
_retry_window = None

def _script_main():
    ui.register("win_focus", _on_win_focus)

def _on_win_focus(toplevel_window: Window):
    _abort_retry()
    _update_scope(toplevel_window)

def _schedule_retry(window: Window):
    """Schedules a retry of assessing the UI framework after a short duration. Raises an exception if a timeout was reached.

    This function changes global state.
    """

    global _retry_job, _retry_start, _retry_window

    if _retry_job:
        TIMEOUT = 2.0
        if time.perf_counter() - _retry_start >= TIMEOUT:
            _retry_job = None
            _retry_window = None

            # Cause `UIFramework.ERROR` up in the call stack.
            raise RuntimeError("Couldn't detect UI framework before timeout.")
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

    _framework = _Detector()(toplevel_window)
    #i Shouldn't raise exceptions (see implementation).

    _ui_framework_scope.update()

    _prepare_active_window(_framework)
    #i Last, because possible exception shouldn't prevent scope update.

@_mod.scope
def _ui_framework_scope():
    global _framework
    return {"ui_framework": str(_framework)}


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

    def __call__(self, toplevel_window: Window) -> UIFramework:
        return self._check_cache_and_toplevel_window(toplevel_window, _schedule_retry)

    def _check_cache_and_toplevel_window(self, toplevel_window: Window, schedule_retry_or_noop: Callable[[Window], None]) -> UIFramework:
        """Reads the top-level window's UI framework from its window properties, or tries to recognize it."""

        FRAMEWORK_INT_PROP_NAME = "Talon.SmartInput.UIFramework"
        ASSESSMENT_TIME_NS_PROP_NAME = "Talon.SmartInput.UIFrameworkAssessmentTimeNS"

        # Read cached assessment, if available.
        if _MUST_CACHE_ASSESSMENT:
            cached_assessment_time_ns = user32.GetPropW(toplevel_window.id, ASSESSMENT_TIME_NS_PROP_NAME)
            if cached_assessment_time_ns and cached_assessment_time_ns >= _script_load_time_ns:
            #i Enum may have been changed before script reload.
                try:
                    framework = UIFramework(user32.GetPropW(toplevel_window.id, FRAMEWORK_INT_PROP_NAME))
                    if framework != UIFramework.PENDING:
                        return framework
                except ValueError:
                    pass

        # Assess.
        try:
            framework = self._check_toplevel_window(toplevel_window, schedule_retry_or_noop)

            # Cache assessment inside window itself.
            if _MUST_CACHE_ASSESSMENT and framework != UIFramework.PENDING:
                for (prop_name, value) in (
                    (FRAMEWORK_INT_PROP_NAME, int(framework)),
                    (ASSESSMENT_TIME_NS_PROP_NAME, time.perf_counter_ns()),  # Dependent on 64-bit process.
                    #i Setting the UI framework first is important. If we'd set the assessment time first and then setting the UI framework failed, we would have presented the old UI framework value as valid in the current context.
                ):
                    success = user32.SetPropW(toplevel_window.id, prop_name, value)
                    if not success:
                        last_error = ctypes.get_last_error()
                        if last_error == winerror.ERROR_INVALID_WINDOW_HANDLE:
                            break
                        else:
                            raise ctypes.WinError(last_error)

                #i Since we only save numeric values, final removal with `RemovePropW()` shouldn't be necessary.
        except Exception:
            # Convert exception into mere output, so the Talon scope can't keep delivering a past framework, which could lead to erratic input behavior. (Actual Talon scope behavior not tested.)
            print(
                "ERROR: Exception during UI framework detection:\n"
                + textwrap.indent(
                    traceback.format_exc()  # Ends with newline.
                    + f"Active top-level window (ID {hex(toplevel_window.id)}): {toplevel_window}",
                    "  ",
                )
            )

            framework = UIFramework.ERROR

        return framework

    def _check_toplevel_window(self, toplevel_window: Window, schedule_retry_or_noop: Callable[[Window], None]) -> UIFramework:
        """Tries to recognize the top-level window's UI framework."""

        class ExtraSource(Enum):
            CHILD_WINDOW_TREE = auto()
            MODULE_FILENAMES = auto()
            UIA_DATA = auto()

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

        toplevel_class = toplevel_window.cls  # Win32 class name.
        match toplevel_class:
            case "#32770":  # Dialog system class.
                # Try again with owner window, if available.
                owner_window = _get_owner_window(toplevel_window)
                #TODO: This regularly detects default open- and save-dialogs incorrectly (e.g., in Balabolka). Maybe check child tree for hints - before owner detection on same PIDs, and after this `case` on different PIDs.
                if owner_window and owner_window.app.pid == toplevel_window.app.pid:
                    framework = self._check_toplevel_window_and_its_cache(owner_window, lambda _: None)
                    if framework.is_concrete:
                        return framework
            case "AutoHotkeyGUI":
                return UIFramework.AUTO_HOTKEY
            case "SunAwtFrame" | "SunAwtDialog":
                return UIFramework.AWT
            case "ThunderRT6FormDC":
                return UIFramework.CLASSIC_VISUAL_BASIC
            case "FLUTTER_RUNNER_WIN32_WINDOW":
                return UIFramework.FLUTTER
            case "gdkWindowToplevel" | "gdkSurfaceToplevel":  # GDK: GIMP Drawing Kit.
                return self._check_module_filenames(toplevel_window.app.pid, {UIFramework.GTK})
            case "MozillaWindowClass":
                return UIFramework.GECKO
            case "SWT_Window0" | "SWT_WindowShadow0":
                return UIFramework.SWT
            case "SALFRAME" | "SALSUBFRAME":
                return UIFramework.VISUAL_CLASS_LIBRARY
            case "Windows.UI.Core.CoreWindow" | "WinUIDesktopWin32WindowClass":
                return UIFramework.WINRT_XAML
            case "wxWindowNR":
                return UIFramework.WX_WIDGETS
            case _:
                if toplevel_class.startswith("ATL:"):
                    return UIFramework.ATL
                elif toplevel_class.startswith("Chrome_WidgetWin_"):
                    return UIFramework.CHROME
                elif toplevel_class.startswith("GlassWndClass-GlassWindowClass-"):
                    return UIFramework.JAVA_FX
                elif _mfc_class_regex.search(toplevel_class):
                    return UIFramework.MFC
                elif _qt_class_regex.search(toplevel_class):
                    return UIFramework.QT
                elif _visual_component_library_class_regex.search(toplevel_class):
                    return UIFramework.VISUAL_COMPONENT_LIBRARY
                elif _winforms_class_regex.search(toplevel_class):
                    return UIFramework.WIN_FORMS
                elif toplevel_class.startswith("HwndWrapper["):
                    # Probably WPF.
                    favor_extra_source(ExtraSource.UIA_DATA)

        framework = UIFramework.UNKNOWN

        for source in extra_sources:
            match source:
                case ExtraSource.CHILD_WINDOW_TREE:
                    framework = self._check_child_window_tree(toplevel_window)
                case ExtraSource.MODULE_FILENAMES:
                    framework = self._check_module_filenames(toplevel_window.app.pid)
                case ExtraSource.UIA_DATA:
                    framework = self._check_uia_data(toplevel_window)

            if framework != UIFramework.UNKNOWN:
                return framework

        if toplevel_class == "ApplicationFrameWindow":
        #i Probably hosted UWP app. (Process A has child windows of process B.)
            # Try again until app hopefully loaded in a recognizable manner.
            schedule_retry_or_noop(toplevel_window)
            framework = UIFramework.PENDING

        return framework

    def _check_child_window_tree(self, toplevel_window: Window, possible_frameworks: Optional[set[UIFramework]] = None) -> UIFramework:
        """Tries to recognize the top-level window's UI framework by its Win32 child window tree."""

        wants_mfc = possible_frameworks is None or UIFramework.MFC in possible_frameworks
        wants_winrt_xaml = possible_frameworks is None or UIFramework.WINRT_XAML in possible_frameworks

        framework = UIFramework.UNKNOWN

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
                framework = UIFramework.MFC
                return False
            if wants_winrt_xaml and child_class in _WINRT_XAML_CHILD_CLASSES:
                # if _retry_job:
                #     print(f"Duration until recognition: {(time.perf_counter() - _retry_start) * 1000:.0f} ms")

                framework = UIFramework.WINRT_XAML
                return False

            return True

        win32gui.EnumChildWindows(toplevel_window.id, handle_child_window, None)
        return framework

    def _check_module_filenames(self, pid, possible_frameworks: Optional[set[UIFramework]] = None) -> UIFramework:
        """Tries to recognize the process's UI framework by its module filenames (mostly DLLs)."""

        wants_gtk = possible_frameworks is None or UIFramework.GTK in possible_frameworks

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
                    return UIFramework.GTK
        finally:
            process_handle.Close()

        return UIFramework.UNKNOWN

    def _check_uia_data(self, toplevel_window: Window, possible_frameworks: Optional[set[UIFramework]] = None) -> UIFramework:
        """Tries to recognize the top-level window's UI framework by its UI Automation data."""

        wants_wpf = possible_frameworks is None or UIFramework.WPF in possible_frameworks

        element = ax.get_element_from_handle(toplevel_window.id)
        framework_id = element.framework_id

        #i - `"Win32"` as the framework ID is often only a placeholder, because no better value is provided.
        #i - It may be that a framework ID other than `"Win32"` is only provided on the child level. (This is, e.g., the case with VS Code.) But care must be taken not to confuse the framework ID of a control-level child (not covering the whole window surface) with the entire window's framework ID (see also other comment talking about DirectUI).
        #i - `element.class_name` may also contain useful information. There may be no corresponding Win32 window for the element, but because this is a different concept than the Win32 class name, the UIA class name can still be available.
        #i - `element.automation_id` may also contain useful information.

        if wants_wpf and framework_id == "WPF":
            return UIFramework.WPF

        return UIFramework.UNKNOWN


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

def _prepare_active_window(framework: UIFramework):
    if framework == UIFramework.QT:
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
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, shared_lparam)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, shared_lparam | (1 << 30) | (1 << 31))
        #i The asynchronous `PostMessage()` instead of the synchronous `SendMessage()` is used, because the calls may take a considerable amount of time (seconds) when an app was just started. Ensuring the window is fully prepared before insertion can even start is desirable, but this function runs inside the `win_focus` event handler, shouldn't delay other handlers, and text insertion while the app window isn't fully loaded yet is improbable.

_script_main()
