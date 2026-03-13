# Windows Toolkit

(Not affiliated with Microsoft)

[TODO]

# Setup

Interacting with suggestion overlays (or respective ghost text) can be subject to race conditions. For both Talon's default `insert()` and Windows Toolkit's replacement, in every app that supports suggestions, it's advisable to turn off accepting suggestions with Enter, so that insertions like `"return\n"` never do something unintended:

- VS Code: Settings (Ctrl+,) > search for setting
  - [`editor.acceptSuggestionOnEnter`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.acceptSuggestionOnEnter) > value "off"
  - Possibly also relevant:
    - [`editor.quickSuggestions`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.quickSuggestions)
    - [`editor.suggestOnTriggerCharacters`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.suggestOnTriggerCharacters) (e.g., on `.`)
    - [`editor.acceptSuggestionOnCommitCharacter`](https://enyium.github.io/uri-tools/v1/load.html?uri=vscode%3A%2F%2Fsettings%2Feditor.acceptSuggestionOnCommitCharacter)
- Notepad++: "Settings" > "Preferences" > "Auto-Completion" > "Insert Selection" > "ENTER"

# Known Issues

- Because of a bug in Talon v0.4 where .py files are reloaded in incorrect order, triggering certain reload chains brings the code into an inconsistent state. This can, e.g., happen when editing a file or using Git to update the repository. Talon's log may then show strange errors (e.g., because of interop of a new Python module instance with an old Python module instance). Restarting Talon solves the issue.

[TODO]
