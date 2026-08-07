# statusline-spark

Two status line renderers for Claude Code. Both add history — the thing the
built-in status line cannot show, because Claude Code hands it a snapshot per
tick and no series.

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install statusline-spark@sniper7kills
```

Then, in a Claude Code session:

```
/statusline-spark:install
```

Restart Claude Code afterwards.

## What you get

**Main status line** — wraps [`claude-powerline`](https://github.com/Owloops/claude-powerline)
and appends two sparklines to its session row:

```
~/src/app  main ✚2  opus  v2.1.0
◷ 34%  $1.82  12m  +240 −31    ◔ ▂▃▅▇▆▂▃▅▆▇  $ ▁▁▃█▁▂▁▁
```

- `◔` — context-window **level** over time. Non-monotonic: it drops hard at every
  auto-compact, so the sawtooth is a visible record of compaction events.
- `$` — cost burn **rate**, the delta between samples. Cumulative cost only rises,
  so plotting the level would be a useless ramp; the delta shows bursts.

Samples are written to `$CLAUDE_CONFIG_DIR/statusline-history/<session>.json`,
one every 15s, 240 retained (~1h), pruned after 3 days.

**Subagent panel** — replaces each agent row with:

```
⟳ code-auditor (opus/high) ███░░░░░ 41% 82.4k ▂▅█▃▂▁▁▂ ⧗4m12s ↑327/s  sweep for TODOs
```

status · name · model/effort · context bar · tokens · per-tick token delta
sparkline (a stalled agent reads as a flat run of `▁`) · elapsed · token rate ·
description. Width-aware: it counts terminal cells, not UTF-16 units, so a CJK or
emoji description does not overflow the panel, and it strips control and bidi
characters out of agent-supplied text before rendering.

## Requirements

- `node` on `PATH`
- `claude-powerline` for the main line's segments:
  `npm install -g @owloops/claude-powerline`

Without `claude-powerline` the main line still renders — you get directory,
model, and the sparklines, and nothing else. The subagent panel does not need it
at all.

## Configuration

Segments come from `$CLAUDE_CONFIG_DIR/claude-powerline.json`, seeded from
[`config/claude-powerline.json`](config/claude-powerline.json) on first install
and never overwritten afterwards. The shipped default uses `charset: "unicode"`
and `style: "minimal"` — readable in a terminal with no Nerd Font. Switch to
`"nerdfont"` and a powerline style if you have the glyphs. See the
[claude-powerline docs](https://github.com/Owloops/claude-powerline) for the full
segment list.

Environment overrides:

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | Config root. Default `~/.claude`. |
| `CLAUDE_POWERLINE_BIN` | Explicit path to the executable, if it is not on `PATH` or in `~/.local/bin`. |
| `CLAUDE_SUBAGENT_STATUSLINE_DEBUG` | File path. Logs each raw stdin payload and emitted row. |

The sparkline attaches to powerline row index 1 (`TARGET_LINE` in
`statusline.js`). If you cut your config down to a single row, set it to `0`.

## Why an install command instead of the plugin manifest

`statusLine` and `subagentStatusLine` are `settings.json` fields — a plugin
manifest has no key for either, so a plugin cannot register a status line the way
it registers hooks or commands. And a plugin's own install path is version
stamped (`…/cache/<marketplace>/<plugin>/<version>`), so a settings entry
pointing into it would break on the next update.

So `/statusline-spark:install` copies the renderers to
`$CLAUDE_CONFIG_DIR/statusline-spark/` — stable, user-owned — and points
`settings.json` there. It backs `settings.json` up first, and reports any
existing status line it displaces.

Check the wiring without changing anything:

```bash
node scripts/install.js --status
```

## Uninstall

```
/statusline-spark:uninstall
```

Removes the settings entries — only if they still point at this plugin — and
deletes the installed renderers. Your `claude-powerline.json` and
`statusline-history/` are left alone.
