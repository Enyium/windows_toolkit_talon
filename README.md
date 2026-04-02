# Windows Toolkit

(Not affiliated with Microsoft)

This is a repository for [Talon](https://talon.wiki/)'s `user` directory with tools like the following (mostly Windows-only):

- The [`insert()` override](/insert/index.py) provides better keyboard input simulation. Among other tricks, it waits for caret (text cursor) standstill before continuing instead of relying on a fixed waiting duration per character.
- The [`user.ui_framework`](/ui_frameworks/detection.py) scope contains the active window's UI framework. It can, e.g., be used to optimize the settings for the `insert()` override. You can check its current value in the "Scope" section of Talon's debug window (run `menu.open_debug_window()`, which [`community`](https://github.com/talonhub/community) binds to the phrase `talon open debug`). For possible values, see the assigned strings in the [`UIFramework` enum](/ui_frameworks/enum.py).
- In the [`win_events`](/win_events/) directory are tools for use in Python code as well as [Talon actions](/win_events/actions.py) that enable you to react on the occurrence or absence of [win events](https://learn.microsoft.com/en-us/windows/win32/winauto/winevents-infrastructure) like, e.g., a window being activated, a UI element acquiring keyboard focus, or the caret location changing.
- The [`pymod_termination`](/pymod_termination/index.py) Python module makes it possible to run code on Talon script reloads. It's actually OS-independent and could thus be transferred into an OS-independent repository if desired and Talon itself doesn't start to offer equivalent functionality.
- The classes [`MessageLoop` and `MessageLoopExecutor`](/lib/message_loop.py) open the door the various Windows functionality.

The [settings](/settings/) allow you to configure the tools. See the tools to see which settings are available.

# Setup

## App Settings for `insert()` Override

With keyboard input simulation, interacting with suggestion overlays (or respective ghost text) can be subject to race conditions. For both Talon's default `insert()` and Windows Toolkit's replacement, in every app that supports suggestions, it's advisable to turn off accepting suggestions with Enter (and possibly other means), so that insertions like `"return\n"` never do something unintended:

- VS Code: Settings (Ctrl+,) > search for setting
  - [`editor.acceptSuggestionOnEnter`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.acceptSuggestionOnEnter) > value "off"
  - Possibly also relevant:
    - [`editor.quickSuggestions`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.quickSuggestions)
    - [`editor.suggestOnTriggerCharacters`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.suggestOnTriggerCharacters) (e.g., on `.`)
    - [`editor.acceptSuggestionOnCommitCharacter`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.acceptSuggestionOnCommitCharacter)
- Notepad++: "Settings" > "Preferences" > "Auto-Completion" > "Insert Selection" > "ENTER"

# Known Issues

- `insert()` override:
  - Some text-inserting voice commands from the [`community` repository](https://github.com/talonhub/community) insert one small segment (like a character) at a time, be it using the `key()` action, which has its fixed waiting duration, or the overridden `insert()` action, which waits for caret standstill on every call, slowing insertion down. `community` should consolidate the segments more. As part of the solution, the author uses the following voice command in [`community/core/keys/keys.talon`](https://github.com/talonhub/community/blob/main/core/keys/keys.talon) (needs a [`user.concat()` implementation](https://github.com/Enyium/windows_toolkit_talon/discussions/1); Talon may be slow after a certain number of consolidated characters, like with `community`'s "uppercase..." voice command or when spelling out numbers digit by digit):
    ```talon
    <user.any_alphanumeric_key> <user.any_alphanumeric_key>+: insert(user.concat(any_alphanumeric_key_list))
    ```
  - As of Feb. 2026, [Rango](https://rango.click/) tries to type the text of ineffective hints via `actions.insert()`. This can impair control of web apps like YouTube. To correct this, change the following in Rango's [`/src/response.py`](https://github.com/david-tejada/rango-talon/blob/main/src/response.py#L33):
    ```py
                case "typeTargetCharacters":
                    actions.insert(request_action["target"]["mark"]["value"])
    ```

    ...to this:

    ```py
                case "typeTargetCharacters":
                    text = request_action["target"]["mark"]["value"]

                    # Ensure web apps remain controllable using letter or number keys when the user installed an `insert()` override that's not based on key events.
                    if text.isalnum() and text.isascii():
                        for ch in text:
                            actions.key(ch)
                    else:
                        actions.insert(text)
    ```
  - In many GTK apps, Unicode supplementary characters (> U+FFFF, often emojis) are ignored. (Same for Talon's original `insert()`. Bug report [filed](https://gitlab.gnome.org/GNOME/gtk/-/issues/8121).)
- Because of a bug in Talon v0.4 where .py files are reloaded in incorrect order, triggering certain reload chains brings the code into an inconsistent state. This can, e.g., happen when editing a file or using Git to update the repository. Talon's log may then show strange errors (e.g., because of interop of a new Python module instance with an old Python module instance). Restarting Talon (or triggering reloads of the files in the correct order) solves the issue.

# Code

- The project uses [Enyium's commenting system](https://github.com/Enyium/commenting-system?tab=readme-ov-file).
- A number of pywin32 functions are implemented incorrectly (specifically with regard to error handling by, e.g., missing a call to `GetLastError()`). Therefore, every newly used function must be verified on [GitHub](https://github.com/search?q=repo%3Amhammond%2Fpywin32+path%3A%2F%5C%2Fsrc%5C%2F%2F+Beep&type=code) and in the Talon REPL first (there may be a disparity between pywin32's newest implementation and that locally installed by Talon). If a pywin32 function is flawed, CFFI must be used.
