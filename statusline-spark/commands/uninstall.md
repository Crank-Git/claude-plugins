---
name: uninstall
description: Remove the statusline-spark status line — unwire settings.json and delete the installed renderers.
allowed-tools: Bash, Read
---

# Uninstall statusline-spark

Run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/install.js" --uninstall
```

It removes `statusLine` / `subagentStatusLine` from `settings.json` **only** when
they still point at this plugin's directory, backs the file up first, and deletes
`$CLAUDE_CONFIG_DIR/statusline-spark/`.

Left in place on purpose, and worth telling the user: `claude-powerline.json`
and `statusline-history/`. Both are user data, and `claude-powerline` may be
wired up separately. Delete them only if the user asks.

Report what the installer printed, and that Claude Code falls back to its default
status line on next start.
