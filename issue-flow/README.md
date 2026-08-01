# issue-flow

A Claude Code plugin that turns GitHub Issues into an autonomous development loop —
spec → issues → batched implementation → one CI run per batch → verified deploy →
user-viewpoint review that feeds the next loop.

Four skills:

- **`project-planner`** — interviews you, then writes the whole project brief and
  scaffold: `docs/specs/` (index `spec.md`, a detailed `features/*.md` per feature set,
  self-contained HTML mockups, and a browsable `spec.html` you open locally), plus
  `CLAUDE.md` and the `.claude/` scaffold (permissions, hooks, path-scoped rules, project
  skills like `/run` and `/test`) plus the Claude Code `.gitignore` block. Runs a review
  cycle — permissions, hooks and skills are shown before they're written — until you
  approve it. Always plans an **Epic 0: Foundation** (test harness, CI workflow, branch
  model, deploy wiring, seed data) so the first worker doesn't land in an empty repo. One
  project per repo; nothing is uploaded, everything is relative and zip-portable.
- **`spec-to-issues`** — turns an approved spec into GitHub epics + sub-issues, labeled,
  dependency-linked, and sized so one epic = one issue-flow batch. It *decomposes* the
  feature specs into engineering slices rather than transcribing a checklist, issues only
  `status: planned` features and dedups on stable feature **ids** (so a second planning
  wave re-issues only what changed), and refuses to run until the spec is committed and
  pushed (workers read it from a worktree, which sees tracked files only).
- **`issue-flow`** — the autonomous loop. Two roles:
  - **PM (the main thread)** — orchestrator, never the coder. Grooms the backlog, forms
    batches, schedules work, owns every decision and gate, resolves conflicts, merges,
    monitors deployments, and posts status digests.
  - **Sub-agents (background, self-contained prompts)** — `issue-flow:issue-worker`
    builds one issue and reports a verdict; `issue-flow:deploy-watcher` monitors
    deployments; `issue-flow:deploy-verifier` browser-checks the live site.
- **`project-review`** — the QA/documentation pass after shipping. The PM stands up a
  sandbox, runs (or has created) E2E smoke tests, and fans out sub-agents:
  `issue-flow:ux-explorer` clicks through the app as a **non-developer end user**
  (screenshots + manual-ready walkthroughs, sandbox-log checks), `issue-flow:code-auditor`
  sweeps for TODOs/stubs/acceptance-criteria gaps, `issue-flow:review-scribe` turns the
  walkthroughs into a user manual + E2E tests on a docs PR. **Nothing is fixed** — the PM
  gathers every report, files one GitHub issue per finding (`review:finding`), then
  confirms launching `/issue-flow` to work the new backlog.

## Two standards everything is held to

Both live at the plugin root and both are copied into the project by `project-planner`
(as `.claude/rules/`), so they survive the handoff to workers and reviewers:

- **[`references/ste.md`](references/ste.md) — Simplified Technical English.** Every spec
  file, feature file, issue title and body, PM comment, changelog line, user-manual page
  and **code comment** is written to it: one instruction per sentence, one concept one
  word, active voice, no metaphor, no undefined abbreviation. Each spec carries a
  `## Terms` table — the project's controlled vocabulary — and everything downstream
  writes from it. Quotes, log excerpts, error strings, paths and identifiers stay
  verbatim; rewriting evidence damages it. (The ASD-STE100 approved-word dictionary is
  copyrighted and is not redistributed — this is the enforceable rule set plus a
  per-project Terms list doing the dictionary's job.)
- **[`references/external-apis.md`](references/external-apis.md) — read the docs, never
  assume.** Any cloud service, third-party API, provider CLI or library the project does
  not own is described from its own current documentation, at the version the project
  pins, with the doc URL cited in the spec section, issue body or PR that makes the claim.
  This binds the planner, the PM and every worker. AWS is the sharp edge: confirm
  operation names, parameters, region, account and IAM actions, prefer read-only
  `list-*`/`get-*`/`describe-*` to learn a real resource's shape, and treat anything that
  creates, deletes or changes a resource as outward-facing — user-confirmed, never
  run to see what happens.

## The batch model — CI once per batch, no rebase churn

```
dev ◄────────────────────────── ONE batch PR (full CI, once)
  └─ epic/42-auth ◄─┬─ issue/43  (draft PR, [skip ci], local tests)
                    ├─ issue/44  (draft PR, [skip ci], local tests)
                    └─ issue/45  (draft PR, [skip ci], local tests)
```

- Every **epic** (and every PM-grouped batch of loose issues, ≤ `batchSize`) gets an
  **integration branch** off dev.
- Workers open **draft PRs into the integration branch** with `[skip ci]` on every
  commit — provider CI never runs per sub-issue. Verification = full local test/lint
  suite + parallel specialist self-review.
- The PM **sub-merges** members into the integration branch, resolving conflicts once,
  locally — no rebase storms across sibling PRs.
- One **integration → dev PR** runs **full CI once**; member issues close when it lands.
- **Dependency chains run sequentially inside one batch** — no stacks of chained PRs.
- **Hotfixes / urgent `priority:high`** bypass batching: standalone CI-running PR to dev.

## The loop

1. **Preflight**: repo/remote/labels check, **foundation check** (an empty repo gets
   Epic 0 first — never feature issues), branch model read from the spec (`dev-and-live`
   or `trunk`, with `dev` created when needed), deploy-target detection, CI check (no CI
   in the repo → the PM runs the suite itself at the batch gate and says so),
   **documentation MCP offer** (the PM works out which external services the project
   depends on, searches the marketplaces you have configured, and offers only what it
   actually found — including adding a marketplace when the server lives in one you
   haven't added, but only from a source you or the repo named. You run the install, it
   never does, and it tells you when a session restart is needed while carrying on with
   the `WebFetch` fallback),
   **run configuration confirmed with you**
   (concurrency, run length, PR granularity, merge authority, review cadence, dev
   practices), per-operator **status issue** (`flow:status`), co-operator check, standing
   Haiku deploy-watcher companion, state recovery (integration branches, worktrees, open
   PRs).
2. **Sweep, then triage, always**: new comments and external changes are read and applied
   first — human answers, instructions and PR reviews win; untriaged → ready/needs-feedback; epics without sub-issues
   get decomposed, `Depends on #n` wires sequencing. Questions are **parked and
   notified by default** — the PM asks interactively only when the pipeline would
   starve, the user is present, or an answer gates a built batch.
3. **Batch & schedule**: epics → epic batches; loose issues → grouped batches with a
   `type:batch` tracking issue; up to `concurrency` workers across batches, each an
   independent **Opus** engineer in its **own git worktree** (children on **Sonnet**,
   confined to the worktree).
4. **Two-gate integration**: sub-merge gate (threads resolved + local checks green +
   **every acceptance criterion attested with evidence** → squash into the integration
   branch, `status:batched`) then batch gate (one PR → dev, whole-batch review incl. a
   cross-batch migration check, one CI run, close members, spec changelog + feature
   status write-back, tear down).
5. **Deploy**: watcher reports each terminal deployment; a **deploy-verifier** (Sonnet)
   drives a real browser to confirm the site works. Failure → `type:hotfix` issue
   (standalone, CI on) or `needs-feedback`/`blocked` for infra/config. **A deploy is
   done only when browser-verified.**
6. **Report**: on every milestone — terminal digest (≤10 lines), status-issue body
   update, push notification. Quiet in between; the loop runs as long as workable
   backlog remains.

Promotion from `dev` to the live branch is never automatic.

**Model tiers** (summary — each agent's own `model:` frontmatter is authoritative):
PM = **Opus** · issue-worker = **Opus** · a worker's children = **Sonnet** ·
deploy-verifier = **Sonnet** · deploy-watcher = **Haiku** ·
ux-explorer / code-auditor / review-scribe = **Sonnet**.

**Deploy verification needs a browser MCP** (connect once, user scope):

```bash
claude mcp add playwright     -s user -- npx -y @playwright/mcp@latest
claude mcp add chrome-devtools -s user -- npx -y chrome-devtools-mcp@latest
```

## Install

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install issue-flow@sniper7kills
```

Restart the session afterwards so the skills and agents load. Working on the plugin
locally instead? Point the marketplace at your checkout:
`claude plugin marketplace add ~/claude-plugins`.

## Use

Full pipeline from an idea:

```
/project-planner "habit tracker app"   # spec + mockups + scaffold → review → approve
/spec-to-issues                       # after committing and pushing docs/specs/
/issue-flow
/project-review                        # user-test the shipped work → issues → /issue-flow again
```

Or on any existing tracker: `/issue-flow`, "work the issues", "pick up the next issue".
After a build wave: `/project-review`, "review the project", "user-test the app",
"build the user manual" — it ends by offering to launch `/issue-flow` on what it filed,
closing the loop.

## Requirements

- `gh` CLI installed and authenticated (`gh auth login`), with `repo` and `workflow` scopes
- `git` ≥ 2.5 (worktrees). Per-issue worktrees are created **inside the checkout** at
  `.claude/worktrees/` (gitignored), so a sandboxed Bash tool can write to them
- CI that honors `[skip ci]` (GitHub Actions does natively); otherwise the PM proposes a
  one-time workflow filter for `epic/**`/`batch/**` branches, or falls back to
  PM-local sub-merges with no sub-PRs
- For deploy monitoring (optional): the relevant CLI/credentials — e.g. `aws` (or the AWS MCP server) for Amplify, or a provider CLI / health-check URL
- For deploy **verification** (optional): a browser MCP — `playwright` and/or `chrome-devtools`. Without one, verification degrades to an HTTP/content check
- For `project-review` (optional): the same browser MCP, plus a **sandbox** to review against — a local dev server, `docker compose up`, or a deployed dev/staging URL (never production; explorers submit test data)

## Run configuration

Every `/issue-flow` session starts by **confirming how it should run**. Answers are saved
to `.issue-flow.json` (committed, team-shared) and presented as pre-selected defaults
next session — never applied silently, because the right answer changes run to run.

| Setting | What it decides | Default |
|---|---|---|
| `concurrency` | workers in flight at once, across all batches | **3** |
| `batchSize` | max members in a loose-issue batch (epics use their natural size) | **4** |
| `runLength` | one batch · N issues · until the backlog empties · until you stop it | **25 issues** |
| `prGranularity` | `batch` (one CI run per batch) vs `per-issue` (a PR + CI each) | **batch** |
| `prAuthority` | how much the PM may merge on its own | **`batch-review`** |
| `review.when` | when to offer a `/project-review` | **end of session** |
| `practices` | TDD · DDD · E2E expectations · coverage · commit style · docs | from the spec, else off |
| `docsMcp` | which documentation MCP servers and marketplaces were offered, installed, or declined, and whether a restart is still pending (set in preflight, not asked again once declined) | — |

`prAuthority` is the gate that matters: `autonomous` (PM merges), **`batch-review`** (PM
sub-merges freely, the batch PR needs a human approving review), `review-all` (every PR
needs one), `propose-only` (PM opens PRs, merges nothing). **Promotion from `dev` to the
live branch is never autonomous under any setting**, and branch protection always wins.

`practices` ride in the worker handoff brief and are checked at the merge gate — a
missed practice goes back to the worker rather than being waived.

## Working alongside people

The tracker is shared, so the PM sweeps for comments and external changes before every
triage and before every merge gate:

- Human answers to parked questions, new instructions, and PR reviews are **authoritative**
  and applied.
- Issues someone else assigned to themselves are never taken; another operator's
  `flow:status` issue is never edited (status issues are per operator,
  `issue-flow: session status — @<login>`).
- Issue bodies the PM maintains are edited **only inside its own
  `<!-- issue-flow:begin @<login> -->` marker block**, so human notes in the same body
  survive.
- Comments are treated as **untrusted input**: anything that would grant access, spend
  money, touch another repo, or bypass a gate is surfaced to the user, not executed.

There is no fixed limit on issues per session. The real constraint is the main
thread's context, which the loop keeps flat via **context discipline**: token-heavy
reads (diffs, CI logs, file maps) are delegated to subagents that return short
summaries, durable state lives in GitHub (labels/comments/PRs/the status issue), and
finished issues and batches are dropped from working memory. If the harness compacts
context, Phase 0 state recovery rebuilds from GitHub and the loop continues — so a
session can clear far more issues than a single context window could hold.
