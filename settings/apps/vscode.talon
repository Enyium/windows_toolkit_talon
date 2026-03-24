app: vscode
-
settings():
    #TODO: WITH VS CODE MAINTAINERS: At least in the SCM input box, after fast input via `SendInput()` with `KEYEVENTF_UNICODE`, the last text check isn't performed, so that erroneously, the result of a previous text check is presented. Maybe rather throttle text checks instead of giving them up when being flooded with input?
    user.wtk_insert__caret_still_before_last_char = true
    #i With the `joshbolduc.commitlint` VS Code extension, VS Code's SCM input box shows errors in commit messages using wavy red underlining. This setting reduces stale underlining that should disappear or extend further after insertion of the final character. The problem could possibly also exist with other extensions or in other parts of the app.

    user.wtk_insert__caret_still_ms = 65
    #i Makes the previous setting more reliable.
