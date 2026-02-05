"""
This file makes the `user.ui_framework` Talon scope available that can be used for window matching. See the `UIFramework` enum for possible string values. The assessments are cached in the windows themselves using window properties. Whenever Talon reloads this file, the assessments are reset (without removing the window properties).
"""

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

    from .lib.winapi import kernel32, user32, w, wapi
else:
    raise NotImplementedError("Unsupported OS.")

#. Debug flags.
_MUST_LOG_ASSESSMENT = False
"""Whether the UI framework assessment should be printed to Talon's log after every window activation (may be read from cache)."""
_MUST_CACHE_ASSESSMENT = True
"""Whether the UI framework assessment is saved to and read from each window's property list. Talon reloading this Python module invalidates previously cached data."""
_MUST_LOG_CHILD_WINDOWS = False
"""Whether each child window before loop abortion should be printed to Talon's log. `True` can be used in conjunction with `_MAY_ABORT_CHILD_WINDOW_LOOP_EARLY = False` to always log all child windows."""
_MAY_ABORT_CHILD_WINDOW_LOOP_EARLY = True
"""Whether the child window loop may be aborted as soon as a UI framework hint was found. As a side effect, if different UI framework hints are present in the child window tree, this may change which one applies."""

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

    #TODO: Use GDK instead of GTK, because it's about event loop matters and automation? (Similar to AWT for Swing, and WinRT XAML for WinUI 3 etc.)
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
_WINRT_XAML_CHILD_CLASSES = frozenset((
    "Windows.UI.Core.CoreWindow",
    #i See also <https://learn.microsoft.com/en-us/uwp/api/windows.ui.core.corewindow>.
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Microsoft.UI.Content.DesktopChildSiteBridge",
))

#. Filenames of loaded modules (DLLs). (Input is lowercased.)
_gtk_dll_regex = re.compile(r"^libgtk-[\d.-]+\.dll$")

_FRAMEWORK_INT_PROP_NAME = w("Talon.SmartInput.UIFramework")
_ASSESSMENT_TIME_NS_PROP_NAME = w("Talon.SmartInput.UIFrameworkAssessmentTimeNS")

_framework = UIFramework.PENDING
"""Last assessment for communication with Talon scope."""

_retry_job = None
_retry_start = 0
_retry_window = None

def _script_main():
    _ui_framework_scope.update()
    app.register("ready", _on_ready)
    ui.register("win_focus", _on_win_focus)

def _on_ready():
    _update_scope(ui.active_window())

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

    if _MUST_LOG_ASSESSMENT:
        print(f"UI framework (window ID {toplevel_window.id:#010x}): {_framework}")

    _ui_framework_scope.update()

@_mod.scope
def _ui_framework_scope():
    global _framework
    return {"ui_framework": str(_framework)}


class _Detector:
    """Calling an instance detects the specified top-level window's UI framework, specifically with regard to event loop matters and automating the window, and not so much with regard to widget libraries, skins, or look and feel.

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

        # Read cached assessment, if available.
        if _MUST_CACHE_ASSESSMENT:
            cached_assessment_time_ns = int(wapi.cast("uintptr_t",
                user32.GetPropW(wapi.cast("HWND", toplevel_window.id), _ASSESSMENT_TIME_NS_PROP_NAME)
            ))
            if cached_assessment_time_ns and cached_assessment_time_ns >= _script_load_time_ns:
            #i Enum may have been changed before script reload.
                try:
                    framework = UIFramework(int(wapi.cast("uintptr_t",
                        user32.GetPropW(wapi.cast("HWND", toplevel_window.id), _FRAMEWORK_INT_PROP_NAME)
                    )))
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
                    (_FRAMEWORK_INT_PROP_NAME, int(framework)),
                    (_ASSESSMENT_TIME_NS_PROP_NAME, time.perf_counter_ns()),  # Dependent on 64-bit process.
                    #i Setting the UI framework first is important. If we'd set the assessment time first and then setting the UI framework failed, we would have presented the old UI framework value as valid in the current context.
                ):
                    success = user32.SetPropW(
                        wapi.cast("HWND", toplevel_window.id),
                        prop_name,
                        wapi.cast("HANDLE", value)
                    )
                    if not success:
                        last_error = kernel32.GetLastError()
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
            OWNER = auto()
            UIA_DATA = auto()

        extra_sources = (ExtraSource.CHILD_WINDOW_TREE,)
        is_dialog = False
        expected_module_based_frameworks = frozenset()  # Not any. Only after other hints.

        toplevel_class = toplevel_window.cls  # Win32 class name.
        match toplevel_class:
            case (
                "#32770"  # Dialog system-class.
                | "NativeHWNDHost"  # At least property-sheet-based dialogs like Windows' `NewLinkHereW()` and `WNetConnectionDialog()`.
            ):
                extra_sources = (ExtraSource.CHILD_WINDOW_TREE, ExtraSource.MODULE_FILENAMES, ExtraSource.OWNER)
                is_dialog = True  # Prevents owner check if module check says standard dialog.
            case "AutoHotkeyGUI":
                return UIFramework.AUTO_HOTKEY
            case "SunAwtFrame" | "SunAwtDialog":
                return UIFramework.AWT
            case "ThunderRT6FormDC":
                return UIFramework.CLASSIC_VISUAL_BASIC
            case "FLUTTER_RUNNER_WIN32_WINDOW":
                return UIFramework.FLUTTER
            case "gdkWindowToplevel" | "gdkSurfaceToplevel":  # GDK: GIMP Drawing Kit.
                extra_sources = (ExtraSource.MODULE_FILENAMES,)
                expected_module_based_frameworks = {UIFramework.GTK}
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
                    extra_sources = (ExtraSource.UIA_DATA, ExtraSource.CHILD_WINDOW_TREE)

        framework = UIFramework.UNKNOWN

        is_std_dialog = False
        for source in extra_sources:
            match source:
                case ExtraSource.CHILD_WINDOW_TREE:
                    framework = self._check_child_window_tree(toplevel_window)

                case ExtraSource.MODULE_FILENAMES:
                    (framework, is_std_dialog) = self._check_module_filenames(
                        toplevel_window,
                        expected_module_based_frameworks,
                        wants_dialog_check=is_dialog,
                    )

                case ExtraSource.OWNER:
                    if not is_std_dialog:
                        owner_window = _get_owner_window(toplevel_window)
                        if owner_window and owner_window.app.pid == toplevel_window.app.pid:
                            owner_framework = self._check_cache_and_toplevel_window(
                                owner_window, lambda _: None
                            )
                            if owner_framework.is_concrete:
                                framework = owner_framework

                case ExtraSource.UIA_DATA:
                    framework = self._check_uia_data(toplevel_window)

            if framework != UIFramework.UNKNOWN:
                if framework == UIFramework.PENDING:
                    schedule_retry_or_noop(toplevel_window)
                return framework

        if (
            framework == UIFramework.UNKNOWN
            and toplevel_class == "ApplicationFrameWindow"
            #i Probably hosted UWP app. (Process A has child windows of process B.)
        ):
            framework = UIFramework.PENDING
            schedule_retry_or_noop(toplevel_window)

        return framework

    def _check_child_window_tree(self, toplevel_window: Window, possible_frameworks: Optional[set[UIFramework]] = None) -> UIFramework:
        """Tries to recognize the top-level window's UI framework by its Win32 child window tree."""

        wants_mfc = possible_frameworks is None or UIFramework.MFC in possible_frameworks
        wants_swt = possible_frameworks is None or UIFramework.SWT in possible_frameworks
        wants_winrt_xaml = possible_frameworks is None or UIFramework.WINRT_XAML in possible_frameworks

        if _MUST_LOG_CHILD_WINDOWS:
            print(f"Traversing top-level window `{toplevel_window}` (ID {toplevel_window.id:#x}).")

        framework = UIFramework.PENDING
        #i Pending as long as we didn't encounter a visible window or a concrete hint. For some apps, some of the time, like Windows Explorer, all child windows are invisible directly after start.
        has_children = False
        has_visible_window = False
        done_retval = not _MAY_ABORT_CHILD_WINDOW_LOOP_EARLY  # `False` breaks loop.

        def handle_child_window(hwnd, _):
            nonlocal framework, has_children, has_visible_window

            try:
                child_class = win32gui.GetClassName(hwnd)

                if not has_visible_window or _MUST_LOG_CHILD_WINDOWS:
                    kernel32.SetLastError(winerror.ERROR_SUCCESS)
                    is_visible = user32.IsWindowVisible(wapi.cast("HWND", hwnd))  # Also checks ancestor visibility.
                    if not is_visible:
                        last_error = kernel32.GetLastError()
                        if last_error:
                            raise ctypes.WinError(last_error)
                    else:
                        has_visible_window = True

                if _MUST_LOG_CHILD_WINDOWS:
                    kernel32.SetLastError(winerror.ERROR_SUCCESS)
                    control_id = user32.GetWindowLongPtrW(wapi.cast("HWND", hwnd), user32.GWLP_ID)
                    if control_id == 0:
                        last_error = kernel32.GetLastError()
                        if last_error:
                            raise ctypes.WinError(last_error)
            except (pywintypes.error, OSError) as e:
                if e.winerror == winerror.ERROR_INVALID_WINDOW_HANDLE:
                    return True  # Continue loop.
                else:
                    raise

            if _MUST_LOG_CHILD_WINDOWS:
                print(f"Child window: {'  visible' if is_visible else 'invisible'}, HWND {hwnd:#010x}, control ID {control_id:#06x}, class \"{child_class}\"")

            has_children = True

            #i The order of the checks can be relevant. See also other comment regarding DirectUI. Let's keep the most conclusive hints at the top.
            if wants_swt and child_class == "SWT_Window0":
            #i Relevant in dialogs with top-level class `#32770`.
                framework = UIFramework.SWT
                return done_retval
            if wants_winrt_xaml and child_class in _WINRT_XAML_CHILD_CLASSES:
                # if _retry_job:
                #     print(f"Duration until recognition: {(time.perf_counter() - _retry_start) * 1000:.0f} ms")

                framework = UIFramework.WINRT_XAML
                return done_retval
            if wants_mfc and _mfc_class_regex.search(child_class):
                framework = UIFramework.MFC
                return done_retval

            return True

        win32gui.EnumChildWindows(toplevel_window.id, handle_child_window, None)
        #i WinAPI function officially doesn't report errors, but `has_children` will be `False`.
        if framework == UIFramework.PENDING and (
            not has_children or has_visible_window
        ):
            framework = UIFramework.UNKNOWN
        if _MUST_LOG_CHILD_WINDOWS and not has_children:
            print("No child windows found.")

        return framework

    def _check_module_filenames(self, toplevel_window: Window, possible_frameworks: Optional[set[UIFramework]] = None, wants_dialog_check: bool = False) -> tuple[UIFramework, bool]:
        """Tries to recognize the process's UI framework by its module filenames (mostly DLLs).
        
        Optionally checks whether the window was created by a Windows DLL known to create standard dialogs. If so, the second return value will be `True`. Besides the case with no such DLL, if a hint for a desired UI framework was found before encountering one of said DLLs, `False` is returned. The argument activating this check should only be set to `True` if the top-level window's class name hints towards a dialog (like `#32770`).
        """

        wants_gtk = possible_frameworks is None or UIFramework.GTK in possible_frameworks
        wants_any_framework = possible_frameworks is None or len(possible_frameworks) != 0

        if wants_dialog_check:
            # Find out module that created the window.
            kernel32.SetLastError(winerror.ERROR_SUCCESS)
            window_module_handle = user32.GetWindowLongPtrW(
                wapi.cast("HWND", toplevel_window.id),
                user32.GWLP_HINSTANCE
            )
            if window_module_handle == 0:
                last_error = kernel32.GetLastError()
                if last_error:
                    raise ctypes.WinError(last_error)

        process_handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            toplevel_window.app.pid,
        )

        plausibly_std_dialog = False
        try:
            module_handles = win32process.EnumProcessModulesEx(
                process_handle,
                win32process.LIST_MODULES_ALL,
            )

            filename_buffer = wapi.new("WCHAR[]", 256)
            #i Maximum path *component* length as per <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation>.

            for module_handle in module_handles:
                success = kernel32.K32GetModuleBaseNameW(
                    wapi.cast("HANDLE", process_handle.handle),
                    wapi.cast("HMODULE", module_handle),
                    filename_buffer,
                    len(filename_buffer),
                )
                if not success:
                    last_error = kernel32.GetLastError()
                    if last_error == winerror.ERROR_INVALID_HANDLE:
                    #i When aggressively loading and unloading DLLs in a test process with a window, this was the only error that occurred; i.e., `EnumProcessModulesEx()` didn't fail. The error is obviously related to unloading a module; when a module was *loaded* while `EnumProcessModulesEx()` ran and wasn't returned, that case must be seen as similar to running this code a few milliseconds earlier when the module also wasn't loaded and apparently can't be handled the same as the unload case.
                        # Give process a bit of time to settle.
                        return (UIFramework.PENDING, plausibly_std_dialog)
                    else:
                        raise ctypes.WinError(last_error)
                filename = wapi.string(filename_buffer).lower()

                if wants_gtk and _gtk_dll_regex.search(filename):
                    return (UIFramework.GTK, plausibly_std_dialog)

                if (
                    wants_dialog_check
                    and module_handle == window_module_handle
                    and filename in {
                        "comctl32.dll",  # At least `TaskDialog()`, `NewLinkHereW()`, `WNetConnectionDialog()`.
                        "comdlg32.dll",  # Open- and save-dialog and many more.
                        "netplwiz.dll",  # `WNetDisconnectDialog()`.
                        "shell32.dll",  # At least `SHBrowseForFolderW()`.
                        "user32.dll",  # `MessageBoxW()`.
                        #i System Informer shows this in a process's properties dialog under the "Windows" tab in the "Module" column.
                    }
                ):
                    plausibly_std_dialog = True
                    if not wants_any_framework:
                        break
        finally:
            process_handle.Close()

        return (UIFramework.UNKNOWN, plausibly_std_dialog)

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

_script_main()
