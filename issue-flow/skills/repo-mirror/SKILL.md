---
name: repo-mirror
description: Keep a project's specs, issues and PRs private while publishing its code to a public mirror. Use when the user says "/repo-mirror", "make the specs private", "set up a public mirror", "mirror this repo", "convert this repo to private + public mirror", "clean the specs out of the public repo", or "import a PR from the mirror". Three modes - setup (a new private repo plus a filtered public mirror kept in sync by a GitHub Actions job), convert (move an existing public repo to that model and remove the specs from its history, always behind a full dry run and an explicit go-ahead), and import (bring an outside contributor's PR from the public mirror into the private repo). GitHub only.
---

# Repo mirror — private source of truth, filtered public mirror

The private repo holds everything: code, `docs/specs/`, `CLAUDE.md`, `.claude/`, the
issue-flow issues and PRs. A GitHub Actions job in the private repo publishes a filtered
copy of chosen branches and all tags to the public repo. issue-flow runs against the
private repo and does not change.

The filter is [`scripts/mirror_filter.py`](scripts/mirror_filter.py): git filter-repo
with a fixed exclude list. It is **deterministic** — the same history gives the same
hashes — so the sync job pushes without `--force`, and the one-time conversion and every
later sync produce the same commits. Never filter with anything else, and pin
`git-filter-repo==2.47.0` everywhere (the template does; locally, check `pip show` or
`brew info`).

**GitHub only.** Gitea needs a different push and transfer story. Stop and say so.

## The config

`.github/mirror/config.json` in the private repo:

```json
{
  "private": "owner/name-private",
  "public": "owner/name",
  "branches": ["main"],
  "exclude": ["docs/specs/", "docs/adr/", "CLAUDE.md", "glob:*/CLAUDE.md", ".claude/",
              ".issue-flow.json", ".github/mirror/", ".github/workflows/mirror.yml"],
  "cutover": 0
}
```

- **`exclude`** — the default list above. Ask the user before you add or drop an entry;
  do not decide what is private for them. The last two entries keep the mirror machinery
  itself out of the mirror. Always keep them.
- **`branches`** — the live branch, plus `dev` under the dev-and-live model. Never the
  `issue/`, `batch/`, `epic/`, `review/` or `spec/` branches.
- **`cutover`** — Unix time of the switch. In commits made after it, `#123` becomes
  `owner/name-private#123`, because private issue numbers restart and would otherwise
  link to an unrelated public issue. Commits before it keep their references unchanged:
  those numbers belong to the public repo. Set it once and never change it — a new value
  rewrites history.

## Mode: setup (new project)

For a project that has no public history yet. The planner hands off here when the user
chose `visibility: mirrored`.

1. Confirm both names. Create the private repo (`gh repo create <private> --private`) and
   the public one (`gh repo create <public> --public`). Both are outward-facing: confirm
   first.
2. Install the files in the private repo:
   - `scripts/mirror_filter.py` → `.github/mirror/mirror_filter.py`;
   - the config → `.github/mirror/config.json`, `cutover` = now;
   - `templates/mirror.yml` → `.github/workflows/mirror.yml`, with `__PRIVATE__`,
     `__PUBLIC__` and `__BRANCHES__` filled in.
3. Guard every workflow that publishes — releases, Pages, packages, container images —
   with `if: github.repository == '<public>'`. Both repos run the same workflow files;
   unguarded, a tag releases twice.
4. Deploy key: `ssh-keygen -t ed25519 -N '' -f <scratch>/mirror_key`, then
   `gh repo deploy-key add <scratch>/mirror_key.pub -R <public> --allow-write -t mirror`
   and `gh secret set MIRROR_DEPLOY_KEY -R <private> < <scratch>/mirror_key`. Delete both
   key files afterwards.
5. Commit and push to every mirrored branch. Watch the `Mirror` run, then confirm the
   public repo has the code and none of the excluded paths.
6. Set `repo:` in `docs/specs/spec.md` to the **private** repo. Issues are filed there.

## Mode: convert (existing public repo)

This rewrites the history of a repo other people use. **Two phases with a hard gate
between them.** Phase A only reads and writes to the scratch directory. Phase B starts
only when the user has read the Phase A report and says go — and approval of Phase A is
not approval of Phase B.

### Why the repo is not just made private

Making a public GitHub repo private permanently erases its stars and watchers and splits
off its forks. So the existing repo stays where it is and becomes the **mirror**; a new
`<name>-private` repo becomes the source of truth.

### Phase A — dry run

Work in the scratch directory. Change nothing on GitHub.

1. `git clone --mirror` the public repo. Write the config with the default exclude list
   and the branches the user names.
2. **History.** `mirror_filter.py stats` on the clone. Also list every path in history
   that looks private but is not excluded — `docs/*.md` design notes, investigation
   write-ups, other agent files (`AGENTS.md`, `.cursor/`) — and ask about each.
3. **Rehearse the filter.** Run `filter` twice into two directories, then `check`.
   Report: both runs give the same hashes for every mirrored ref; commits before and
   after; commits pruned; old → new hash of every tag; how many signed commits lose
   their signature (`git log --format=%G?`, anything not `N`) — the Verified badge goes.
4. **Rehearse the first sync.** Push the filtered result to a local bare repo. Add a
   commit to a copy of the source, filter again, push again without `--force`. It must
   fast-forward.
5. **Secrets.** Run `gitleaks detect --log-opts=--all` on the clone if it is installed,
   else grep the excluded paths' full history for key patterns (`ghp_`, `github_pat_`,
   `AKIA`, `-----BEGIN`, `xox[bp]-`, `password`/`token`/`secret` assignments). Any hit
   means: rotate the credential. A rewrite does not make a published secret secret.
6. **Refs.** Branches on the public repo that the mirror does not keep (they get
   deleted), open PRs whose head is in the public repo (a force-push breaks them), tags
   that move.
7. **Releases.** `gh release list`. A release follows its tag name, so it survives the
   tag moving. List release notes that quote spec text.
8. **Workflows.** Every workflow that publishes (tags, Pages, packages, deploys) — each
   needs the repository guard. List repository secrets and variables
   (`gh secret list`, `gh variable list`): the private repo needs the ones its own CI uses.
9. **Issues.** Split them: issues the owner or issue-flow opened move to the private repo
   (`gh issue transfer` — public to private is allowed, the reverse is not); issues
   outside users opened stay public. Name each outside author. Labels must exist in the
   target first or they drop off.
10. **PRs.** They cannot be transferred or deleted by the owner. List PRs whose **body
    quotes spec text** (not just a `docs/specs/` link — a dead path leaks nothing) — the
    user may edit those, and GitHub keeps the edit history, which the owner can delete
    revision by revision. List PRs whose **diff touches excluded paths**: their
    `refs/pull/*` keep the old commits reachable, and only GitHub Support can remove
    them.
11. **Forks.** Each fork, with its creation date against the date the specs first
    appeared. Forks made after it hold a copy, and nothing can recall it. Say so plainly.
12. **The plan.** Write `convert-report.md` in the scratch directory: every finding
    above, the exact Phase B commands in order with real values filled in, the user
    decisions still open, and a draft GitHub Support request (sensitive-data removal:
    the repo, the PR numbers from step 10, a request to purge cached views and
    unreachable commits). Show the user the report. Stop.

### Phase B — execute (only on the user's explicit go)

Re-run step 1 and steps 3–4 first if anything was pushed to the public repo since
Phase A. Then, in order, confirming any step the report left open:

1. **Freeze.** Ask the user to merge nothing and push nothing to the public repo until
   step 6 is done.
2. **Private repo.** `gh repo create <private> --private`, then push the unfiltered
   history from the mirror clone: `git push <private-url> 'refs/heads/*:refs/heads/*'
   'refs/tags/*:refs/tags/*'` (never `--mirror`: `refs/pull/*` is read-only on GitHub).
   Copy the secrets and variables the private repo's CI needs.
3. **Install** the setup files on every mirrored branch of the private repo (setup steps
   2–3), with `cutover` = now.
4. **Filter** the private repo locally with the pinned filter-repo, then `check`.
5. **Force-push** the result to the public repo: each mirrored branch and every tag with
   `--force`, then delete the public branches the mirror does not keep. This is the one
   force-push in the life of the mirror.
6. **Deploy key** (setup step 4), then run the `Mirror` workflow by hand
   (`gh workflow run mirror.yml -R <private>`). It must report every ref up to date. If
   it pushes anything, CI and the local run disagree — stop and investigate.
7. **Issues.** `gh label clone <public> -R <private>`, then transfer the agreed issues.
8. **Repoint.** The user's local clone: `git remote set-url origin <private-url>`.
   `docs/specs/spec.md` `repo:` and any issue-flow config → the private repo.
9. **Clean-up the user owns:** the PR body edits they chose, and the Support request.
   Hand them the draft; do not submit it for them.
10. **Verify.** The public repo's default branch shows no excluded path; its releases
    still list their assets; `check` passes on a fresh clone of the public repo.

## Mode: import (outside contribution)

A PR opened on the public mirror:

1. `gh pr diff <n> -R <public> --patch > <scratch>/pr-<n>.patch`.
2. In the private repo, branch from the PR's target branch and `git am -3` the patch.
   Authorship is kept. The patch never touches an excluded path, so it applies.
3. Open the PR in the private repo. Write the public PR fully qualified
   (`<public>#<n>`) in its title and body — a bare `#n` is rewritten to point at the
   private repo.
4. After it merges and the mirror syncs, comment on the public PR with the mirror commit
   that carries the change, thank the author, and close it.

## Rules

- **Phase B never runs on the same turn as Phase A**, and never without the user saying
  go after reading the report.
- **One force-push, ever** — Phase B step 5. A later sync that is rejected means
  divergence. Find the cause; do not force.
- **Tell the truth about what a rewrite cannot do:** forks, clones and archives keep the
  old history. The conversion stops further exposure. It does not recall anything.
