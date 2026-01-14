# Smart Input

[TODO]

# Setup

Interacting with suggestion overlays can be subject to race conditions. For both Talon's default `insert()` and Smart Input's replacement, in every app that supports suggestions, it's very advisable to turn off accepting suggestions with Enter, so that insertions like `"return\n"` never do something unintended:

- VS Code: Settings (Ctrl+,) > search for `editor.acceptSuggestionOnEnter` > value "off"
- Notepad++: "Settings" > "Preferences" > "Auto-Completion" > "Insert Selection" > "ENTER"

[TODO]
