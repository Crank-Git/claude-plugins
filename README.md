# Sniper7Kills LLC — Claude Code plugins

A Claude Code plugin marketplace. Add it once, then install any plugin it lists.

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install issue-flow@sniper7kills
```

Restart the session after installing so the plugin's skills and agents load.

## Plugins

| Plugin | What it does |
|---|---|
| [**issue-flow**](issue-flow/) | Autonomous development loop driven by GitHub Issues: plan a spec, decompose it into epics and sub-issues, build them with background worker agents on batched integration branches (one CI run per batch), verify the deploy in a real browser, then user-test the result and file what it finds. |

## Working on a plugin here

Validate a plugin or the marketplace manifest before committing:

```bash
claude plugin validate .                 # the marketplace manifest
claude plugin validate ./issue-flow      # one plugin and all its components
```

Tag a release once `plugin.json` and the marketplace entry agree on the version:

```bash
claude plugin tag ./issue-flow --push
```

To try a change without publishing it, add this checkout as a local marketplace:

```bash
claude plugin marketplace add ~/claude-plugins
```

## License

MIT — see [LICENSE](LICENSE).
