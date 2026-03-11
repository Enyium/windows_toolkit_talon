"""
Shows Windows standard dialogs for the purpose of investigating them with regard to writing dialog recognition code and for testing that code.
"""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import win32con
import win32api

from ...lib.winapi import w
from .. import dlgcon
from .winapi import appwiz, comctl32, kernel32, mpr, user32, wapi

_running_exe_path = Path(win32api.GetModuleFileNameW(None))


def _script_main():
    running_exe_name = _running_exe_path.name.lower()
    match running_exe_name:
        case "talon.exe":
            from talon import Module

            mod = Module()

            @mod.action_class
            class Actions:
                def si_show_std_dialogs():
                    """Shows the standard dialogs that the code currently defines to show, so you can investigate them with spy tools or test the code that is meant to recognize them."""
                    _show_dialogs()

        case "pythonw.exe" | "python.exe":
            success = user32.SetProcessDpiAwarenessContext(wapi.cast("DPI_AWARENESS_CONTEXT", user32.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
            if not success:
                raise ctypes.WinError(kernel32.GetLastError())

            _show_dialog_by_args()

        case _:
            raise RuntimeError(f'Unexpected executable name "{running_exe_name}".')


def _show_dialogs():
    """In this function, you can specify which dialogs should be shown when the Talon action runs. Comment in and out accordingly."""

    #i The `GWLP_HINSTANCE` filenames of the dialog top-level windows are relevant for the function checking loaded modules during UI framework detection.

    # _spawn_dialog_subprocess(_WinuserDialogs.message_box_min)
    _spawn_dialog_subprocess(_WinuserDialogs.message_box_max)
    # _spawn_dialog_subprocess(_ControlLibraryDialogs.task_dialog_min)
    # _spawn_dialog_subprocess(_ControlLibraryDialogs.task_dialog_max)
    # _spawn_dialog_subprocess(_ControlLibraryDialogs.task_dialog_like_message_box)

    # _spawn_dialog_subprocess(_WNetDialogs.wnet_connection_dialog)
    # _spawn_dialog_subprocess(_WNetDialogs.wnet_disconnect_dialog)
    # _spawn_dialog_subprocess(_AppWizardCPLDialogs.new_link_here)


def _spawn_dialog_subprocess(static_method):
    """Spawns a child Python process running this script file and tells it to show the specified dialog, so the dialog can't block Talon and spy tools don't have problems spying on the window as some do, perhaps because of Talon's UIAccess privileges."""

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [
                str(Path(env["APPDATA"]) / "talon"),
                env.get("PYTHONPATH", ""),
                #i (If some package shipped with Talon is missing, maybe add the `sys.path` entries as well, possibly together with certain `pythonw.exe` switches.)
            ],
        )
    )

    # fmt: off
    process = subprocess.Popen(
        [
            _running_exe_path.with_name("pythonw.exe"),
            "-B",  # Don't write .pyc files in `__pycache__` directories.
            "-u",  # Unbuffered stdout/stderr.
            "-X", "utf8",  # Send UTF-8 to parent.
            "-m", __name__,  # Module relative to search path above.
            "--", static_method.__qualname__
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        #i Stderr goes to stdout, because the streams can otherwise be intermingled (with to transfer threads) and Talon's log doesn't indicate the `talon.exe` streams anyways.
        stdin=subprocess.DEVNULL,
        bufsize=1,  # Line-buffered.
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # fmt: on

    if process.stdout is None:
        raise RuntimeError("Stdout pipe didn't work.")

    def transfer_lines(source_stream, target_stream, line_prefix: str):
        for line in iter(source_stream.readline, ""):
            target_stream.write(line_prefix + line)
            #i Line already ends with newline character.
        source_stream.close()

    line_prefix = f"PID {process.pid}: "
    Thread(
        target=transfer_lines,
        args=(process.stdout, sys.stdout, line_prefix),
        daemon=True,
    ).start()


def _show_dialog_by_args():
    try:
        double_dash_index = sys.argv.index("--")
        method_path = sys.argv[double_dash_index + 1]
        class_name, method_name = method_path.split(".", 1)
    except (ValueError, IndexError):
        raise ValueError(
            "Missing or incorrect method path. Expected: `-- ClassName.method_name`."
        )

    try:
        cls = globals()[class_name]
    except KeyError:
        raise ValueError(f"Invalid class `{class_name}`.")

    try:
        func = getattr(cls, method_name)
    except AttributeError:
        raise ValueError(f"Invalid method `{class_name}.{method_name}`.")

    func()


class _WinuserDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/>."""

    @staticmethod
    def message_box_min():
        button = user32.MessageBoxW(
            wapi.NULL,
            wapi.NULL,
            "MessageBoxW()",
            win32con.MB_OK,
        )
        if not button:
            raise ctypes.WinError(kernel32.GetLastError())
        #i Similar: `MessageBoxExW()`, `MessageBoxIndirectW()`.

    @staticmethod
    def message_box_max():
        """Try changing the button set inside."""

        button_set = (
            win32con.MB_ABORTRETRYIGNORE
            # user32.MB_CANCELTRYCONTINUE
            # win32con.MB_OK
            # win32con.MB_OKCANCEL
            # win32con.MB_RETRYCANCEL
            # win32con.MB_YESNO
            # win32con.MB_YESNOCANCEL
        )

        button = user32.MessageBoxW(
            wapi.NULL,
            "Message with\n\nmultiple lines.",
            "MessageBoxW()",
            win32con.MB_ICONWARNING | button_set | win32con.MB_HELP,
        )
        if not button:
            raise ctypes.WinError(kernel32.GetLastError())


class _ControlLibraryDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/controls/individual-control-info>."""

    @staticmethod
    def task_dialog_min():
        config = wapi.new("TASKDIALOGCONFIG *")
        config.cbSize = wapi.sizeof("TASKDIALOGCONFIG")

        hresult = comctl32.TaskDialogIndirect(
            config,
            wapi.NULL,
            wapi.NULL,
            wapi.NULL,
        )
        if hresult < 0:
            raise ctypes.WinError(hresult)
        #i Similar: `TaskDialog()`.

    @staticmethod
    def task_dialog_max():
        """Try commenting in some code inside."""

        config = wapi.new("TASKDIALOGCONFIG *")
        config.cbSize = wapi.sizeof("TASKDIALOGCONFIG")
        config.hwndParent = wapi.NULL
        config.hInstance = wapi.NULL

        config.dwFlags = (
            comctl32.TDF_ENABLE_HYPERLINKS
            | comctl32.TDF_ALLOW_DIALOG_CANCELLATION
            # | comctl32.TDF_USE_COMMAND_LINKS
            #i Or `TDF_USE_COMMAND_LINKS_NO_ICON`. Both allow for second line in custom-button texts.
            # | comctl32.TDF_EXPAND_FOOTER_AREA  # Different `pszExpandedInformation` position.
            # | comctl32.TDF_EXPANDED_BY_DEFAULT
            | comctl32.TDF_SHOW_MARQUEE_PROGRESS_BAR
            | comctl32.TDF_CAN_BE_MINIMIZED
        )

        config.dwCommonButtons = (
            comctl32.TDCBF_OK_BUTTON
            | comctl32.TDCBF_YES_BUTTON
            | comctl32.TDCBF_NO_BUTTON
            | comctl32.TDCBF_CANCEL_BUTTON
            | comctl32.TDCBF_RETRY_BUTTON
            | comctl32.TDCBF_CLOSE_BUTTON
        )

        window_title = w("TaskDialogIndirect()")
        config.pszWindowTitle = window_title

        config.DUMMYUNIONNAME.pszMainIcon = wapi.cast("PCWSTR", comctl32.TD_ERROR_ICON)

        main_instruction = w("Main Instruction")
        config.pszMainInstruction = main_instruction

        content = w('Content with\n\n<A HREF="https://example.com/">links</A> at will.')
        config.pszContent = content

        custom_buttons = wapi.new("TASKDIALOG_BUTTON[]", 2)
        custom_buttons[0].nButtonID = dlgcon.psh1
        button_0_text = w("Any number of...")# + "\nNote")
        custom_buttons[0].pszButtonText = button_0_text
        custom_buttons[1].nButtonID = dlgcon.psh2
        button_1_text = w("custom buttons possible.")# + "\nNote")
        custom_buttons[1].pszButtonText = button_1_text
        config.cButtons = len(custom_buttons)
        config.pButtons = custom_buttons
        config.nDefaultButton = 0

        custom_radio_buttons = wapi.new("TASKDIALOG_BUTTON[]", 2)
        custom_radio_buttons[0].nButtonID = dlgcon.rad1
        radio_button_0_text = w("Any number of custom...")
        custom_radio_buttons[0].pszButtonText = radio_button_0_text
        custom_radio_buttons[1].nButtonID = dlgcon.rad2
        radio_button_1_text = w("radio buttons possible.")
        custom_radio_buttons[1].pszButtonText = radio_button_1_text
        config.cRadioButtons = len(custom_radio_buttons)
        config.pRadioButtons = custom_radio_buttons
        config.nDefaultRadioButton = 0

        verification_text = w("Verification text")
        config.pszVerificationText = verification_text

        expanded_information = w('Expanded information\n\nwith <A HREF="notepad.exe">links</A> at will.')
        config.pszExpandedInformation = expanded_information
        expanded_control_text = w("Expanded control text")
        config.pszExpandedControlText = expanded_control_text
        collapsed_control_text = w("Collapsed control text")
        config.pszCollapsedControlText = collapsed_control_text

        config.DUMMYUNIONNAME2.pszFooterIcon = wapi.cast("PCWSTR", comctl32.TD_WARNING_ICON)
        footer = w('Footer with\n\n<A HREF="ms-settings:">links</A> at will.')
        config.pszFooter = footer

        config.pfCallback = wapi.NULL
        config.lpCallbackData = 0
        config.cxWidth = 0

        verification_flag_checked = wapi.new("BOOL *")
        hresult = comctl32.TaskDialogIndirect(
            config,
            wapi.NULL,
            wapi.NULL,
            verification_flag_checked,
        )
        if hresult < 0:
            raise ctypes.WinError(hresult)

    @staticmethod
    def task_dialog_like_message_box():
        """The `MessageBoxW()` dialog doesn't contain DirectUI elements. However, the message box shown when Windows' open- or save-dialog complains about an invalid filename does. They are very similar to each other. The message box this method shows has an identical child window tree to the latter, but additionally shows an icon in the title bar. So, it also shouldn't be what the open- or save-dialog uses. However, both are created by `comctl32.dll`. So, maybe, there's something we're overlooking."""

        config = wapi.new("TASKDIALOGCONFIG *")
        config.cbSize = wapi.sizeof("TASKDIALOGCONFIG")
        config.hwndParent = wapi.NULL
        config.hInstance = wapi.NULL

        config.dwFlags = comctl32.TDF_ALLOW_DIALOG_CANCELLATION
        config.dwCommonButtons = comctl32.TDCBF_OK_BUTTON

        window_title = w("Open")
        config.pszWindowTitle = window_title

        config.DUMMYUNIONNAME.pszMainIcon = wapi.cast("PCWSTR", comctl32.TD_WARNING_ICON)

        content = w("<>\nThe file name is not valid.")
        config.pszContent = content

        hresult = comctl32.TaskDialogIndirect(
            config,
            wapi.NULL,
            wapi.NULL,
            wapi.NULL,
        )
        if hresult < 0:
            raise ctypes.WinError(hresult)


class _CommonDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/dlgbox/common-dialog-box-library>."""

    pass

    #i ChooseColorW()
    #i ChooseFontW()
    #i FindTextW()
    #i GetOpenFileNameW()
    #i     See `GetSaveFileNameW()`.
    #i GetSaveFileNameW()
    #i     AI: Important flags:
    #i         OFN_EXPLORER: “Explorer-style” UI (instead of very outdated)
    #i         OFN_ALLOWMULTISELECT: multi-select noticeably changes layout/interaction
    #i         OFN_ENABLEHOOK / OFN_ENABLETEMPLATE / OFN_ENABLETEMPLATEHANDLE: hook/custom templates (dialog can deviate significantly)
    #i PageSetupDlgW()
    #i PrintDlgW() / PrintDlgExW()
    #i ReplaceTextW()


class _ShellFunctionDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/shell/functions>."""

    pass

    #i SHBrowseForFolderW()
    #i     AI: Important flags:
    #i         BIF_NEWDIALOGSTYLE / BIF_USENEWUI: “newer” UI vs. old tree UI
    #i         BIF_EDITBOX: edit field (UX clearly different)
    #i         BIF_NONEWFOLDERBUTTON: without “New Folder” button (changes flow)
    #i ShellAboutW()
    #i SHMultiFileProperties()
    #i SHOpenWithDialog()
    #i ShowShareFolderUIW()  # Should show the same dialog as `SHObjectProperties()`.
    #i SoftwareUpdateMessageBox()


class _DeprecatedShellAPIDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/shell/deprecated-api>."""

    pass

    #i FileOpen  # Linked incorrectly on <https://learn.microsoft.com/en-us/windows/win32/shell/deprecated-api>. Probably one of the other open-dialogs.
    #i GetFileNameFromBrowse()
    #i PickIconDlg()
    #i RestartDialog() / RestartDialogEx()
    #i ShellFldSetExt  # Linked incorrectly on <https://learn.microsoft.com/en-us/windows/win32/shell/deprecated-api>. Associated with `CLSID_ShellFldSetExt` as per `ShlGuid.h`.
    #i ShellMessageBoxW()
    #i SHFormatDrive()
    #i SHMessageBoxCheckW()
    #i SHObjectProperties()
    #i SHOpenPropSheetW()
    #i SHStartNetConnectionDialogW()


class _ShellInterfaceDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/shell/interfaces>."""

    pass

    #i IFileOpenDialog
    #i     With and without `FOS_PICKFOLDERS` flag
    #i IFileSaveDialog
    #i     With and without `FOS_PICKFOLDERS` flag?
    #i IProgressDialog
    #i IOperationsProgressDialog
    #i IActionProgressDialog
    #i IApplicationAssociationRegistrationUI


class _IShellDispatchDialogs:
    """Source listings:
    - <https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch>
    - <https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch2-object>
    - <https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch4>
    - <https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch5>
    - <https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch6>
    """

    pass

    #i BrowseForFolder()
    #i FileRun()
    #i FindComputer()
    #i FindFiles()
    #i FindPrinter()
    #i SearchCommand()
    #i SetTime()
    #i ShutdownWindows()
    #i TrayProperties()
    #i WindowsSecurity()
    #i WindowSwitcher()


class _WNetDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/wnet/wnet-functions>."""

    @staticmethod
    def wnet_connection_dialog():
        result = mpr.WNetConnectionDialog(wapi.NULL, mpr.RESOURCETYPE_DISK)
        if result != mpr.NO_ERROR and result != -1 & 0xFFFF_FFFF:
            raise RuntimeError("`WNetConnectionDialog()` failed.")

    @staticmethod
    def wnet_disconnect_dialog():
        result = mpr.WNetDisconnectDialog(wapi.NULL, mpr.RESOURCETYPE_DISK)  # Returns immediately.
        if result != mpr.NO_ERROR and result != -1 & 0xFFFF_FFFF:
            raise RuntimeError("`WNetDisconnectDialog()` failed.")

        button = user32.MessageBoxW(
            wapi.NULL,
            'This message box keeps the "Disconnect Network Drives"/WNetDisconnectDialog() dialog alive.',
            "Life Support",
            win32con.MB_OK,
        )
        if not button:
            raise ctypes.WinError(kernel32.GetLastError())

    #i WNetConnectionDialog1W()
    #i WNetDisconnectDialog1W()


class _SecurityAndIdentityDialogs:
    """Source listing: <https://learn.microsoft.com/en-us/windows/win32/api/_security/>."""

    pass

    #i CertSelectCertificateW()
    #i CertViewPropertiesW()
    #i CredUIPromptForCredentialsW()
    #i CredUIPromptForWindowsCredentialsW()
    #i CryptUIDlgCertMgr()
    #i CryptUIDlgSelectCertificateFromStore()
    #i CryptUIDlgViewCertificateW()
    #i DSEditSecurity()
    #i EditSecurity()
    #i EditSecurityAdvanced()
    #i GetOpenCardNameW()
    #i IAzObjectPicker
    #i KeyCredentialManagerShowUIOperation()
    #i OpenPersonalTrustDBDialog()
    #i OpenPersonalTrustDBDialogEx()
    #i SCardUIDlgSelectCardW()


class _AppWizardCPLDialogs:
    """Undocumented functions."""

    @staticmethod
    def new_link_here():
        fd, temp_file_path = tempfile.mkstemp(prefix="temp_shortcut_")
        os.close(fd)
        appwiz.NewLinkHereW(wapi.NULL, wapi.NULL, temp_file_path, win32con.SW_SHOWNORMAL)
        #i Confirming the dialog will keep the file. Canceling it will delete it.

    #i More available.


#i WinRT context:
#i - <https://learn.microsoft.com/en-us/windows/apps/develop/ui/display-ui-objects>
#i - <https://learn.microsoft.com/en-us/uwp/api/windows.security.authentication.web.webauthenticationbroker>
#i - (More?)
#i - Calling WinRT APIs: <https://pywinrt.readthedocs.io/>
#i
#i More:
#i - Dialogs from .cpl files like, e.g., the "Sound" dialog from `mmsys.cpl`.
#i - <https://learn.microsoft.com/en-us/windows/win32/api/_com/>
#i
#i Keywords to search for dialog-presenting APIs on pages with functions/interfaces and their descriptions: dlg, dialog, box, show, choose, sheet, prop.


_script_main()
