from ..lib.str_carrying_one_based_int_enum import StrCarryingOneBasedIntEnum

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
