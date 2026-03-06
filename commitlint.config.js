export default {
    "extends": ["@commitlint/config-conventional"],
    "rules": {
        "scope-enum": [2, "always", [
            "insert",
            "readme",
            "ui-frameworks",
            "win-events",
        ]],
    },
}
