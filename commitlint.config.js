const DISABLED = 0;
const WARNING = 1;
const ERROR = 2;

export default {
    "extends": ["@commitlint/config-conventional"],
    "rules": {
        "scope-enum": [ERROR, "always", [
            "commitlint",
            "gitignore",
            "insert",
            "pymod-termination",
            "readme",
            "tooling",
            "ui-frameworks",
            "win-events",
            "window-patterns",
        ]],
        "body-max-line-length": [DISABLED],
        "footer-max-line-length": [DISABLED],
    },
}
