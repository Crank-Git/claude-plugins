# Worktree isolation (why workers use `isolation: "worktree"`)

issue-flow runs several workers at once against one checkout. Isolation between them is
not advisory — it is the thing that makes concurrency safe. This file records how the
harness models it, and the failure mode that made this rule necessary.

## The rule

- The PM launches every worker with `isolation: "worktree"` and passes **no worktree path**.
- Nobody in the run calls `EnterWorktree` or `ExitWorktree` — not the PM, not a worker,
  not a worker's child.
- A worker's children are spawned with **no `isolation` parameter**; they inherit the
  worker's worktree.

## Why: two different pins

The harness resolves the worktree a command is confined to as
`agentWorktree ?? session.worktreePath`. Those two are not the same thing:

| | set by | scope |
|---|---|---|
| `agentWorktree` | `isolation: "worktree"` at spawn | **that agent only** |
| `session.worktreePath` | `EnterWorktree` | **the whole session** — PM and every live worker |

`EnterWorktree` writes the session-scoped one. In a run with N concurrent workers that is
one variable with N+1 writers, and the last caller wins.

You can tell which one fired from the refusal text: `This agent is isolated in the
worktree …` is the per-agent pin working correctly. `This session is isolated in the
worktree …` means someone called `EnterWorktree` and the whole run is now pinned to one
worker's directory.

## The failure mode this prevents

Measured in a real run (ja4plus, 2026-08-07, Claude Code 2.1.224) where workers were
spawned without `isolation` and created their own worktrees with `EnterWorktree`:

- The session started clean at the repo root and stayed clean for 26 minutes.
- A worker called `EnterWorktree` 5 seconds after it was spawned. The **PM's** working
  directory moved into that worker's tree at the same instant.
- 20 seconds later a second worker called `EnterWorktree`, and the **first worker's** own
  working directory moved into the second worker's tree.
- From then on the PM's `git -C <checkout>` commands were refused, and the PM had to run
  an `EnterWorktree` → `ExitWorktree(keep)` bounce four times to keep working.
- One merged PR carried duplicated code because the guard refused the worker's writes to
  the file the shared helper belonged in.

Renaming worktrees does not help: the pin is on the session, not on the path.

## What `isolation: "worktree"` does instead

Verified by direct measurement on 2.1.224, with two workers running concurrently:

- Each agent gets its own worktree at `.claude/worktrees/agent-<id>`, created, `locked`,
  and cleaned up by the harness.
- Neither agent's directory moved when the other started or finished.
- The PM's working directory never moved, and `git -C <checkout>` from the PM returned
  exit 0 throughout — including compound commands.
- Each agent's own `git -C <checkout>` was refused with the **`This agent is isolated`**
  wording. Cross-worktree writes stay blocked; that is the guard working as intended.
- A child agent spawned with no `isolation` parameter ran in its parent's worktree, on
  the parent's branch, and could write there.

## The one thing you must do yourself

The harness branches a new worktree from the **default branch**, governed by the
`worktree.baseRef` setting (`fresh`, the default, or `head`). It does not know about the
batch's integration branch. So a worker's first git action points its worktree at the
base from its brief:

```bash
git fetch <remote>
git checkout -B issue/<number>-<slug> <base>
```

This was verified to succeed inside a harness-created worktree.

## Related settings

Project `.claude/settings.json`:

- `worktree.baseRef` — `fresh` (default, branches from `origin/<default-branch>`) or
  `head` (branches from local HEAD, carrying unpushed work).
- `worktree.symlinkDirectories` — directories symlinked from the main checkout instead of
  re-materialized per worktree (`node_modules`, `.venv`, `.cache`). Off by default.
- `worktree.sparsePaths` — sparse-checkout cone for large monorepos.
- `worktree.bgIsolation` — `worktree` (default) blocks Edit/Write in the main checkout
  from background sessions until they isolate; `none` disables that guard for the repo.

`.worktreeinclude` at the repo root is read by the harness, which copies the matching
gitignored files (`.env`, local secrets) into each worktree it creates. Keep it accurate
instead of copying those files by hand.
