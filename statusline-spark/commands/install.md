---
name: install
description: Install the statusline-spark status line — copy the renderers into the Claude config directory and wire statusLine and subagentStatusLine in settings.json.
allowed-tools: Bash, Read, Edit
---

# Install statusline-spark

Run the installer:

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/install.js"
```

It copies the two renderers to `$CLAUDE_CONFIG_DIR/statusline-spark/`, seeds
`claude-powerline.json` only if the user has none, backs up `settings.json`, and
sets `statusLine` + `subagentStatusLine`.

Then report to the user:

1. Each line the installer printed.
2. If it reported `claude-powerline NOT found`, say the status line works now but
   renders a bare fallback, and offer to run
   `npm install -g @owloops/claude-powerline`. Ask before installing anything
   globally — do not run it unprompted.
3. If it reported `replaced previous statusLine`, quote the old command so the
   user knows exactly what was displaced and where the backup is.
4. That the change takes effect on the next Claude Code start.

If the installer exits non-zero because `settings.json` does not parse, do not
try to repair the file silently — show the error and ask the user how to
proceed.

To tune which segments appear, edit `$CLAUDE_CONFIG_DIR/claude-powerline.json`;
`README.md` in `${CLAUDE_PLUGIN_ROOT}` documents the layout. Use
`node "${CLAUDE_PLUGIN_ROOT}/scripts/install.js" --status` to check wiring
without changing anything.
