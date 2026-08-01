# Sniper7Kills LLC — Claude Code plugins

A Claude Code plugin marketplace. Add it once, then install any plugin it lists.

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install issue-flow@sniper7kills
```

Restart the session after you install a plugin, so its skills and agents load.

## Plugins

| Plugin | What it does |
|---|---|
| [**issue-flow**](issue-flow/) | Drives an autonomous development loop from GitHub Issues. It plans a spec, decomposes it into epics and sub-issues, and builds each one with a background worker agent on a batched integration branch. CI runs once per batch. A browser check verifies the deployment, and a user-viewpoint review files what it finds. |

## Develop a plugin in this repository

Validate a manifest before you commit it:

```bash
claude plugin validate .                 # the marketplace manifest
claude plugin validate ./issue-flow      # one plugin and all its components
```

Test a change without publishing it. Add this checkout as a local marketplace:

```bash
claude plugin marketplace add ~/claude-plugins
```

Release a version. First set the same version in the plugin's `plugin.json` and in its
entry in `.claude-plugin/marketplace.json`. Then create the tag:

```bash
claude plugin tag ./issue-flow --push
```

The command validates that the two versions agree before it tags.

## License

MIT — see [LICENSE](LICENSE).
