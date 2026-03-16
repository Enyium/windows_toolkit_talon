from cffi import FFI
from typing import Any, cast

wapi = FFI()
CData = wapi.CData

def w(string: str | None) -> CData:
    return cast(CData, wapi.NULL) if string is None else wapi.new("WCHAR[]", string)
    #i `FFI.NULL` from CFFI v1.15's `api.pyi` is typed as `CType`, although it's `isinstance(wapi.NULL, wapi.CData)` that returns `True`.

GENERAL_WAPI_SOURCE = r"""
    #define S_OK                                   0L
    #define NO_ERROR 0L

    // `kernel32.dll`.
    DWORD WINAPI GetLastError(VOID);
    VOID WINAPI SetLastError(DWORD dwErrCode);
"""
"""Code for reuse."""
wapi.cdef(GENERAL_WAPI_SOURCE)

kernel32: Any = wapi.dlopen("kernel32.dll")
wapi.cdef(r"""
    DWORD WINAPI GetTickCount(VOID);
    DWORD WINAPI K32GetModuleBaseNameW(
        HANDLE hProcess,
        HMODULE hModule,
        LPWSTR lpBaseName,
        DWORD nSize
    );
""")

oleacc: Any = wapi.dlopen("oleacc.dll")
wapi.cdef(r"""
    typedef unsigned short VARTYPE;

    typedef struct tagVARIANT {
        union {
            struct _tagVARIANT {
                VARTYPE vt;
                WORD wReserved1;
                WORD wReserved2;
                WORD wReserved3;
                union {
                    LONG lVal;  // `VT_I4`.
                    struct _tagBRECORD {  // Needed for correct struct size.
                        PVOID pvRecord;
                        void *pRecInfo;  // Simplified.
                    } __VARIANT_NAME_4;
                    //i Left out many members.
                } _VARIANT_NAME_3;
            } _VARIANT_NAME_2;
            //i Left out `decVal`.
        } _VARIANT_NAME_1;
    } VARIANT, *LPVARIANT, VARIANTARG, *LPVARIANTARG;

    extern HRESULT __stdcall AccessibleObjectFromEvent(HWND hwnd, DWORD dwId, DWORD dwChildId, void** ppacc, VARIANT* pvarChild);
    //i Simplified `ppacc`.

    #define	ROLE_SYSTEM_TITLEBAR	0x1
    #define	ROLE_SYSTEM_MENUBAR	0x2
    #define	ROLE_SYSTEM_SCROLLBAR	0x3
    #define	ROLE_SYSTEM_GRIP	0x4
    #define	ROLE_SYSTEM_SOUND	0x5
    #define	ROLE_SYSTEM_CURSOR	0x6
    #define	ROLE_SYSTEM_CARET	0x7
    #define	ROLE_SYSTEM_ALERT	0x8
    #define	ROLE_SYSTEM_WINDOW	0x9
    #define	ROLE_SYSTEM_CLIENT	0xa
    #define	ROLE_SYSTEM_MENUPOPUP	0xb
    #define	ROLE_SYSTEM_MENUITEM	0xc
    #define	ROLE_SYSTEM_TOOLTIP	0xd
    #define	ROLE_SYSTEM_APPLICATION	0xe
    #define	ROLE_SYSTEM_DOCUMENT	0xf
    #define	ROLE_SYSTEM_PANE	0x10
    #define	ROLE_SYSTEM_CHART	0x11
    #define	ROLE_SYSTEM_DIALOG	0x12
    #define	ROLE_SYSTEM_BORDER	0x13
    #define	ROLE_SYSTEM_GROUPING	0x14
    #define	ROLE_SYSTEM_SEPARATOR	0x15
    #define	ROLE_SYSTEM_TOOLBAR	0x16
    #define	ROLE_SYSTEM_STATUSBAR	0x17
    #define	ROLE_SYSTEM_TABLE	0x18
    #define	ROLE_SYSTEM_COLUMNHEADER	0x19
    #define	ROLE_SYSTEM_ROWHEADER	0x1a
    #define	ROLE_SYSTEM_COLUMN	0x1b
    #define	ROLE_SYSTEM_ROW	0x1c
    #define	ROLE_SYSTEM_CELL	0x1d
    #define	ROLE_SYSTEM_LINK	0x1e
    #define	ROLE_SYSTEM_HELPBALLOON	0x1f
    #define	ROLE_SYSTEM_CHARACTER	0x20
    #define	ROLE_SYSTEM_LIST	0x21
    #define	ROLE_SYSTEM_LISTITEM	0x22
    #define	ROLE_SYSTEM_OUTLINE	0x23
    #define	ROLE_SYSTEM_OUTLINEITEM	0x24
    #define	ROLE_SYSTEM_PAGETAB	0x25
    #define	ROLE_SYSTEM_PROPERTYPAGE	0x26
    #define	ROLE_SYSTEM_INDICATOR	0x27
    #define	ROLE_SYSTEM_GRAPHIC	0x28
    #define	ROLE_SYSTEM_STATICTEXT	0x29
    #define	ROLE_SYSTEM_TEXT	0x2a
    #define	ROLE_SYSTEM_PUSHBUTTON	0x2b
    #define	ROLE_SYSTEM_CHECKBUTTON	0x2c
    #define	ROLE_SYSTEM_RADIOBUTTON	0x2d
    #define	ROLE_SYSTEM_COMBOBOX	0x2e
    #define	ROLE_SYSTEM_DROPLIST	0x2f
    #define	ROLE_SYSTEM_PROGRESSBAR	0x30
    #define	ROLE_SYSTEM_DIAL	0x31
    #define	ROLE_SYSTEM_HOTKEYFIELD	0x32
    #define	ROLE_SYSTEM_SLIDER	0x33
    #define	ROLE_SYSTEM_SPINBUTTON	0x34
    #define	ROLE_SYSTEM_DIAGRAM	0x35
    #define	ROLE_SYSTEM_ANIMATION	0x36
    #define	ROLE_SYSTEM_EQUATION	0x37
    #define	ROLE_SYSTEM_BUTTONDROPDOWN	0x38
    #define	ROLE_SYSTEM_BUTTONMENU	0x39
    #define	ROLE_SYSTEM_BUTTONDROPDOWNGRID	0x3a
    #define	ROLE_SYSTEM_WHITESPACE	0x3b
    #define	ROLE_SYSTEM_PAGETABLIST	0x3c
    #define	ROLE_SYSTEM_CLOCK	0x3d
    #define	ROLE_SYSTEM_SPLITBUTTON	0x3e
    #define	ROLE_SYSTEM_IPADDRESS	0x3f
    #define	ROLE_SYSTEM_OUTLINEBUTTON	0x40
""")

user32: Any = wapi.dlopen("user32.dll")
wapi.cdef(r"""
    // `MapVirtualKeyW()` constants missing in `pywin32`.
    #define MAPVK_VK_TO_VSC_EX  4

    // Various bare functions.
    HANDLE WINAPI GetPropW(HWND hWnd, LPCWSTR lpString);
    BOOL WINAPI SetPropW(HWND hWnd, LPCWSTR lpString, HANDLE hData);
    BOOL WINAPI IsChild(HWND hWndParent, HWND hWnd);
    BOOL WINAPI IsWindowVisible(HWND hWnd);
    BOOL WINAPI PostThreadMessageW(DWORD idThread, UINT Msg, WPARAM wParam, LPARAM lParam);
    UINT WINAPI RegisterWindowMessageW(LPCWSTR lpString);

    // `GetGUIThreadInfo()`.
    typedef struct tagRECT {
        LONG    left;
        LONG    top;
        LONG    right;
        LONG    bottom;
    } RECT, *PRECT, *LPRECT;

    typedef struct tagGUITHREADINFO {
        DWORD   cbSize;
        DWORD   flags;
        HWND    hwndActive;
        HWND    hwndFocus;
        HWND    hwndCapture;
        HWND    hwndMenuOwner;
        HWND    hwndMoveSize;
        HWND    hwndCaret;
        RECT    rcCaret;
    } GUITHREADINFO, *PGUITHREADINFO, *LPGUITHREADINFO;

    BOOL WINAPI GetGUIThreadInfo(DWORD idThread, PGUITHREADINFO pgui);

    // `GetWindowLongPtrW()`.
    #define GWLP_WNDPROC        -4
    #define GWLP_HINSTANCE      -6
    #define GWLP_HWNDPARENT     -8
    #define GWLP_USERDATA       -21
    #define GWLP_ID             -12

    LONG_PTR WINAPI GetWindowLongPtrW(HWND hWnd, int nIndex);

    // `PeekMessageW()`, `GetMessageW()`.
    #define PM_NOREMOVE         0x0000
    #define WM_QUIT                         0x0012

    typedef struct tagPOINT {
        LONG  x;
        LONG  y;
    } POINT, *PPOINT, *LPPOINT;

    typedef struct tagMSG {
        HWND        hwnd;
        UINT        message;
        WPARAM      wParam;
        LPARAM      lParam;
        DWORD       time;
        POINT       pt;
    } MSG, *PMSG, *LPMSG;

    BOOL WINAPI PeekMessageW(LPMSG lpMsg, HWND hWnd, UINT wMsgFilterMin, UINT wMsgFilterMax, UINT wRemoveMsg);
    BOOL WINAPI GetMessageW(LPMSG lpMsg, HWND hWnd, UINT wMsgFilterMin, UINT wMsgFilterMax);

    // `SendInput()`.
    typedef struct tagMOUSEINPUT {
        LONG    dx;
        LONG    dy;
        DWORD   mouseData;
        DWORD   dwFlags;
        DWORD   time;
        ULONG_PTR dwExtraInfo;
    } MOUSEINPUT, *PMOUSEINPUT, *LPMOUSEINPUT;

    typedef struct tagKEYBDINPUT {
        WORD    wVk;
        WORD    wScan;
        DWORD   dwFlags;
        DWORD   time;
        ULONG_PTR dwExtraInfo;
    } KEYBDINPUT, *PKEYBDINPUT, *LPKEYBDINPUT;

    typedef struct tagHARDWAREINPUT {
        DWORD   uMsg;
        WORD    wParamL;
        WORD    wParamH;
    } HARDWAREINPUT, *PHARDWAREINPUT, *LPHARDWAREINPUT;

    #define INPUT_MOUSE     0
    #define INPUT_KEYBOARD  1
    #define INPUT_HARDWARE  2

    typedef struct tagINPUT {
        DWORD   type;
        union {
            MOUSEINPUT      mi;
            KEYBDINPUT      ki;
            HARDWAREINPUT   hi;
        } DUMMYUNIONNAME;
    } INPUT, *PINPUT, *LPINPUT;

    UINT WINAPI SendInput(
        UINT cInputs,
        LPINPUT pInputs,
        int cbSize
    );

    // `SendMessageTimeoutW()`.
    #define SMTO_ERRORONEXIT    0x0020
    LRESULT WINAPI SendMessageTimeoutW(
        HWND hWnd,
        UINT Msg,
        WPARAM wParam,
        LPARAM lParam,
        UINT fuFlags,
        UINT uTimeout,
        PDWORD_PTR lpdwResult
    );

    // `SetWinEventHook()`, `UnhookWinEvent()`.
    typedef HANDLE HWINEVENTHOOK;

    typedef VOID (__stdcall BARE_WINEVENTPROC)(
        HWINEVENTHOOK hWinEventHook,
        DWORD         event,
        HWND          hwnd,
        LONG          idObject,
        LONG          idChild,
        DWORD         idEventThread,
        DWORD         dwmsEventTime
    );
    typedef BARE_WINEVENTPROC *WINEVENTPROC;

    #define EVENT_SYSTEM_DESKTOPSWITCH      0x0020
    #define EVENT_SYSTEM_SWITCHER_APPGRABBED    0x0024
    #define EVENT_SYSTEM_SWITCHER_APPOVERTARGET 0x0025
    #define EVENT_SYSTEM_SWITCHER_APPDROPPED    0x0026
    #define EVENT_SYSTEM_SWITCHER_CANCELLED     0x0027
    #define EVENT_SYSTEM_IME_KEY_NOTIFICATION  0x0029
    #define EVENT_SYSTEM_END        0x00FF
    #define EVENT_OEM_DEFINED_START     0x0101
    #define EVENT_OEM_DEFINED_END       0x01FF
    #define EVENT_UIA_EVENTID_START         0x4E00
    #define EVENT_UIA_EVENTID_END           0x4EFF
    #define EVENT_UIA_PROPID_START          0x7500
    #define EVENT_UIA_PROPID_END            0x75FF
    #define EVENT_CONSOLE_CARET             0x4001
    #define EVENT_CONSOLE_UPDATE_REGION     0x4002
    #define EVENT_CONSOLE_UPDATE_SIMPLE     0x4003
    #define EVENT_CONSOLE_UPDATE_SCROLL     0x4004
    #define EVENT_CONSOLE_LAYOUT            0x4005
    #define EVENT_CONSOLE_START_APPLICATION 0x4006
    #define EVENT_CONSOLE_END_APPLICATION   0x4007
    #define EVENT_CONSOLE_END       0x40FF
    #define EVENT_OBJECT_INVOKED                0x8013
    #define EVENT_OBJECT_TEXTSELECTIONCHANGED   0x8014
    #define EVENT_OBJECT_CONTENTSCROLLED        0x8015
    #define EVENT_SYSTEM_ARRANGMENTPREVIEW      0x8016
    #define EVENT_OBJECT_CLOAKED                0x8017
    #define EVENT_OBJECT_UNCLOAKED              0x8018
    #define EVENT_OBJECT_LIVEREGIONCHANGED      0x8019
    #define EVENT_OBJECT_HOSTEDOBJECTSINVALIDATED 0x8020
    #define EVENT_OBJECT_DRAGSTART              0x8021
    #define EVENT_OBJECT_DRAGCANCEL             0x8022
    #define EVENT_OBJECT_DRAGCOMPLETE           0x8023
    #define EVENT_OBJECT_DRAGENTER              0x8024
    #define EVENT_OBJECT_DRAGLEAVE              0x8025
    #define EVENT_OBJECT_DRAGDROPPED            0x8026
    #define EVENT_OBJECT_IME_SHOW               0x8027
    #define EVENT_OBJECT_IME_HIDE               0x8028
    #define EVENT_OBJECT_IME_CHANGE             0x8029
    #define EVENT_OBJECT_TEXTEDIT_CONVERSIONTARGETCHANGED 0x8030
    #define EVENT_OBJECT_END                    0x80FF
    #define EVENT_AIA_START                     0xA000
    #define EVENT_AIA_END                       0xAFFF

    #define     OBJID_QUERYCLASSNAMEIDX -12
    #define     OBJID_NATIVEOM      -16

    HWINEVENTHOOK WINAPI SetWinEventHook(
        DWORD eventMin,
        DWORD eventMax,
        HMODULE hmodWinEventProc,
        WINEVENTPROC pfnWinEventProc,
        DWORD idProcess,
        DWORD idThread,
        DWORD dwFlags
    );
    BOOL WINAPI UnhookWinEvent(HWINEVENTHOOK hWinEventHook);
""")
