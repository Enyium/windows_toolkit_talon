from cffi import FFI
from typing import Optional

wapi = FFI()

def w(string: Optional[str]):
    return wapi.NULL if string is None else wapi.new("WCHAR[]", string)

GENERAL_WAPI_SOURCE = r"""
    #define S_OK                                   0L
    #define NO_ERROR 0L

    // `kernel32.dll`.
    DWORD WINAPI GetLastError(VOID);
    VOID WINAPI SetLastError(DWORD dwErrCode);
"""
"""Code for reuse."""
wapi.cdef(GENERAL_WAPI_SOURCE)

kernel32 = wapi.dlopen("kernel32.dll")
wapi.cdef(r"""
    DWORD WINAPI K32GetModuleBaseNameW(
        HANDLE hProcess,
        HMODULE hModule,
        LPWSTR lpBaseName,
        DWORD nSize
    );
""")

user32 = wapi.dlopen("user32.dll")
wapi.cdef(r"""
    // `MapVirtualKeyW()` constants missing in `pywin32`.
    #define MAPVK_VK_TO_VSC_EX  4

    // Various bare functions.
    HANDLE WINAPI GetPropW(HWND hWnd, LPCWSTR lpString);
    BOOL WINAPI SetPropW(HWND hWnd, LPCWSTR lpString, HANDLE hData);
    BOOL WINAPI IsWindowVisible(HWND hWnd);

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
""")
