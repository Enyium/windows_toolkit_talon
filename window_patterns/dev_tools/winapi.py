from cffi import FFI
from typing import Any

from ...lib import winapi as lib_winapi

wapi = FFI()
wapi.cdef(lib_winapi.GENERAL_WAPI_SOURCE)

# def MAKEINTRESOURCEW(i: int) -> CData:
#     return wapi.cast("LPWSTR", i & 0xFFFF)

appwiz: Any = wapi.dlopen("appwiz.cpl")
wapi.cdef(r"""
    void WINAPI NewLinkHereW(
        HWND hwnd,
        HINSTANCE hAppInstance,
        LPWSTR lpwszCmdLine,
        int nCmdShow
    );
    //i Source for signature: <https://github.com/selfrender/Windows-Server-2003/blob/master/shell/cpls/appwzdui/appwiz.c#L617>.
""")

comctl32: Any = wapi.dlopen("comctl32.dll")
wapi.cdef(pack=1, csource=r"""
    //i `cdef()` argument `pack=1` required by source's `#include <pshpack1.h>`.

    enum _TASKDIALOG_FLAGS {
        TDF_ENABLE_HYPERLINKS               = 0x0001,
        TDF_USE_HICON_MAIN                  = 0x0002,
        TDF_USE_HICON_FOOTER                = 0x0004,
        TDF_ALLOW_DIALOG_CANCELLATION       = 0x0008,
        TDF_USE_COMMAND_LINKS               = 0x0010,
        TDF_USE_COMMAND_LINKS_NO_ICON       = 0x0020,
        TDF_EXPAND_FOOTER_AREA              = 0x0040,
        TDF_EXPANDED_BY_DEFAULT             = 0x0080,
        TDF_VERIFICATION_FLAG_CHECKED       = 0x0100,
        TDF_SHOW_PROGRESS_BAR               = 0x0200,
        TDF_SHOW_MARQUEE_PROGRESS_BAR       = 0x0400,
        TDF_CALLBACK_TIMER                  = 0x0800,
        TDF_POSITION_RELATIVE_TO_WINDOW     = 0x1000,
        TDF_RTL_LAYOUT                      = 0x2000,
        TDF_NO_DEFAULT_RADIO_BUTTON         = 0x4000,
        TDF_CAN_BE_MINIMIZED                = 0x8000,
        TDF_NO_SET_FOREGROUND               = 0x00010000,
        TDF_SIZE_TO_CONTENT                 = 0x01000000
    };
    typedef int TASKDIALOG_FLAGS;

    #define TD_WARNING_ICON         0xffff
    #define TD_ERROR_ICON           0xfffe
    #define TD_INFORMATION_ICON     0xfffd
    #define TD_SHIELD_ICON          0xfffc

    enum _TASKDIALOG_COMMON_BUTTON_FLAGS {
        TDCBF_OK_BUTTON            = 0x0001,
        TDCBF_YES_BUTTON           = 0x0002,
        TDCBF_NO_BUTTON            = 0x0004,
        TDCBF_CANCEL_BUTTON        = 0x0008,
        TDCBF_RETRY_BUTTON         = 0x0010,
        TDCBF_CLOSE_BUTTON         = 0x0020
    };
    typedef int TASKDIALOG_COMMON_BUTTON_FLAGS;

    typedef struct _TASKDIALOG_BUTTON {
        int     nButtonID;
        PCWSTR  pszButtonText;
    } TASKDIALOG_BUTTON;

    typedef HRESULT (__stdcall *PFTASKDIALOGCALLBACK)(
        HWND hwnd,
        UINT msg,
        WPARAM wParam,
        LPARAM lParam,
        LONG_PTR lpRefData
    );

    typedef struct _TASKDIALOGCONFIG {
        UINT        cbSize;
        HWND        hwndParent;
        HINSTANCE   hInstance;
        TASKDIALOG_FLAGS                dwFlags;
        TASKDIALOG_COMMON_BUTTON_FLAGS  dwCommonButtons;
        PCWSTR      pszWindowTitle;
        union {
            HICON   hMainIcon;
            PCWSTR  pszMainIcon;
        } DUMMYUNIONNAME;
        PCWSTR      pszMainInstruction;
        PCWSTR      pszContent;
        UINT        cButtons;
        const TASKDIALOG_BUTTON  *pButtons;
        int         nDefaultButton;
        UINT        cRadioButtons;
        const TASKDIALOG_BUTTON  *pRadioButtons;
        int         nDefaultRadioButton;
        PCWSTR      pszVerificationText;
        PCWSTR      pszExpandedInformation;
        PCWSTR      pszExpandedControlText;
        PCWSTR      pszCollapsedControlText;
        union {
            HICON   hFooterIcon;
            PCWSTR  pszFooterIcon;
        } DUMMYUNIONNAME2;
        PCWSTR      pszFooter;
        PFTASKDIALOGCALLBACK pfCallback;
        LONG_PTR    lpCallbackData;
        UINT        cxWidth;
    } TASKDIALOGCONFIG;

    HRESULT WINAPI TaskDialogIndirect(
        const TASKDIALOGCONFIG *pTaskConfig,
        int *pnButton,
        int *pnRadioButton,
        BOOL *pfVerificationFlagChecked
    );
    HRESULT WINAPI TaskDialog(
        HWND hwndOwner,
        HINSTANCE hInstance,
        PCWSTR pszWindowTitle,
        PCWSTR pszMainInstruction,
        PCWSTR pszContent,
        TASKDIALOG_COMMON_BUTTON_FLAGS dwCommonButtons,
        PCWSTR pszIcon,
        int *pnButton
    );
""")

kernel32: Any = wapi.dlopen("kernel32.dll")

mpr: Any = wapi.dlopen("mpr.dll")
wapi.cdef(r"""
    #define RESOURCETYPE_DISK       0x00000001
    DWORD WINAPI WNetConnectionDialog(HWND hwnd, DWORD dwType);
    DWORD WINAPI WNetDisconnectDialog(HWND hwnd, DWORD dwType);
""")

user32: Any = wapi.dlopen("user32.dll")
wapi.cdef(r"""
    // `MessageBoxW()`.
    #define MB_CANCELTRYCONTINUE        0x00000006L
    int WINAPI MessageBoxW(
        HWND hWnd,
        LPCWSTR lpText,
        LPCWSTR lpCaption,
        UINT uType
    );

    // `SetProcessDpiAwarenessContext()`.
    typedef HANDLE DPI_AWARENESS_CONTEXT;
    #define DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2  -4
    BOOL WINAPI SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT value);
""")
