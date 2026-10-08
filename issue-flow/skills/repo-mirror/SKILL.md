---
name: repo-mirror
description: Keep a project's specs, issues and PRs private while publishing its code to a public mirror. Use when the user says "/repo-mirror", "make the specs private", "set up a public mirror", "mirror this repo", "convert this repo to private + public mirror", "clean the specs out of the public repo", or "import a PR from the mirror". Three modes - setup (a new private repo plus a filtered public mirror kept in sync by a GitHub Actions job), convert (move an existing public repo to that model and remove the specs from its history - either the existing repo becomes the mirror, or, for a repo with no forks and few stars, it is renamed and made private and a new public repo takes its name; always behind a full dry run and an explicit go-ahead), and import (bring an outside contributor's PR from the public mirror into the private repo). GitHub only.
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
  itself out of the mirror. Always keep them. Once the mirror is live, a changed list
  rewrites every commit that touches the affected paths, so every sync after it is
  rejected until the one-time repair in Rules runs. Say so before you change it.
- **`branches`** — the live branch, plus `dev` under the dev-and-live model. Never the
  `issue/`, `batch/`, `epic/`, `review/` or `spec/` branches. Only tags that one of
  these branches contains are mirrored; a tag on any other branch is skipped.
- **`cutover`** — Unix time of the switch. In commits made after it, `#123` becomes
  `owner/name-private#123`, because private issue numbers restart and would otherwise
  link to an unrelated public issue. Commits before it keep their references unchanged:
  those numbers belong to the public repo. `0` (as in the template) means not set: no
  message is rewritten — that is what Phase A rehearses with. Set it once, when the
  private repo goes live, and never change it — a new value rewrites history.

## Requirements

- **Actions must run on the private repo.** GitHub bills Actions minutes on private
  repos. If the account's payments failed or its spending limit is spent, every job is
  refused with "The job was not started because recent account payments have failed or
  your spending limit needs to be increased" — and the mirror never syncs. A sync takes
  well under a minute, but ask the user to confirm the account can run private-repo
  Actions before you set anything up. The first manual `Mirror` run proves it.
- **A deploy key with write access** on the public repo. Its pushes start the public
  repo's workflows, which is what lets a tag release from the public repo.

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

**How the sync behaves.** Every run publishes every mirrored branch and every tag those
branches contain, not only the ref that started it. Runs share one concurrency group,
and GitHub cancels a queued run when a newer one queues behind it — a `cancelled` Mirror
run is normal, and the run that replaced it carries its refs. A tag that is moved or
re-created in the private repo diverges from the mirror's copy, and the sync is rejected
until the repair in Rules runs.

## Mode: convert (existing public repo)

This rewrites the history of a repo other people use. **Two phases with a hard gate
between them.** Phase A only reads and writes to the scratch directory. Phase B starts
only when the user has read the Phase A report and says go — and approval of Phase A is
not approval of Phase B.

### Two routes

Making a public GitHub repo private permanently erases its stars and watchers, and its
forks stay public as separate repos. That decides the route.

- **Mirror route (the default).** The existing repo stays public and becomes the
  **mirror**; a new `<name>-private` repo becomes the source of truth. It keeps the stars
  and the fork network. Its costs: the issues must be transferred (they get new numbers),
  and the PRs stay public — a PR cannot be transferred or deleted by the owner, so diffs
  and bodies that show private material stay readable until GitHub Support removes them.
- **Flip route (rename and recreate).** The existing repo is renamed to `<name>-private`
  and made private; a new public repo is created under the old name and receives the
  filtered history. Every issue and PR becomes private in one step, with its number
  unchanged — nothing to transfer, no PR exposure, no Support request. Its costs: the
  stars and watchers are lost, and the new public repo starts bare — its settings,
  secrets, environments, rulesets, releases and outside integrations must be set up
  again.

**The flip route requires zero forks.** A fork keeps the full history public no matter
what happens to the parent, so with a fork the flip hides nothing that the mirror route
does not. With zero forks, offer the flip route when the stars are few enough that the
user accepts losing them — that threshold is the user's call, not yours. Otherwise use
the mirror route.

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
   Ask whether the account can run Actions on private repos (see Requirements); the plan
   is dead without it.
9. **Issues.** Split them: issues the owner or issue-flow opened move to the private repo
   (`gh issue transfer` — public to private is allowed, the reverse is not); issues
   outside users opened stay public. Name each outside author. Labels must exist in the
   target first or they drop off; they show on the moved issue a few seconds after the
   transfer.
10. **PRs.** They cannot be transferred or deleted by the owner. List PRs whose **body
    quotes spec text** (not just a `docs/specs/` link — a dead path leaks nothing) — the
    user may edit those, and GitHub keeps the edit history, which the owner can delete
    revision by revision. List PRs whose **diff touches excluded paths**: their
    `refs/pull/*` keep the old commits reachable, and only GitHub Support can remove
    them.
11. **Forks.** Each fork, with its creation date against the date the specs first
    appeared. Forks made after it hold a copy, and nothing can recall it. Say so plainly.
12. **Route.** Stars, watchers and forks decide it (see Two routes). With zero forks,
    present both routes with their costs and recommend one. For the flip route, also
    list what the new public repo must get again: description, homepage and topics;
    secret and variable names; environments (`gh api repos/<repo>/environments`);
    rulesets and branch protection; webhooks (`gh api repos/<repo>/hooks`); Pages;
    releases with their notes and assets (`gh release list`); and outside integrations
    that are connected to the repo (Cloudflare or Vercel deploys, npm trusted
    publishing, Codecov, other GitHub Apps) — the API cannot list all of these, so ask
    the user. Under either route, a package published **with provenance** names commit
    hashes that the rewrite removes, so its "source commit" links go dead; say so.
13. **The plan.** Write `convert-report.md` in the scratch directory: every finding
    above, the exact Phase B commands in order with real values filled in, the user
    decisions still open, and a draft GitHub Support request (sensitive-data removal:
    the repo, the PR numbers from step 10, a request to purge cached views and
    unreachable commits — mirror route only). Show the user the report. Stop.

### Phase B — execute (only on the user's explicit go)

Re-run step 1 and steps 3–4 first if anything was pushed to the public repo since
Phase A. Then follow the route the user chose, in order, confirming any step the report
left open.

**Keep Actions off on any repo whose tags you push before its workflows are ready.** A
tag push starts that repo's tag-triggered workflows — releases and package publishes —
once per tag, and a force-pushed tag starts them again. That holds for the public repo
during the history push, and for the private repo until its publishing workflows carry
the repository guard and its deploy key is set. Turning Actions off does not block git
access. Off: `gh api -X PUT repos/<repo>/actions/permissions -F enabled=false`. On: the
same with `enabled=true`.

**After a visibility change, wait for git access.** For a short time after `gh repo edit
--visibility`, pushes fail with "Repository ... is disabled". Wait until
`git ls-remote <url>` succeeds before the next push.

#### Mirror route

1. **Freeze.** Ask the user to merge nothing and push nothing to the public repo until
   step 6 is done.
2. **Private repo.** `gh repo create <private> --private`, turn its Actions off, then push
   the unfiltered history from the mirror clone: `git push <private-url> 'refs/heads/*:refs/heads/*'
   'refs/tags/*:refs/tags/*'` (never `--mirror`: `refs/pull/*` is read-only on GitHub).
   Copy the secrets and variables the private repo's CI needs.
3. **Install** the setup files on every mirrored branch of the private repo (setup steps
   2–3), with `cutover` = now.
4. **Filter** a fresh `git clone --mirror` of the private repo with the pinned
   filter-repo, then `check`. Not a working clone: a stale or unpushed local branch would
   give a history that CI does not reproduce.
5. **Force-push** the result to the public repo, with its Actions off: each mirrored
   branch and every tag with `--force`, then delete the public branches the mirror does
   not keep. This is the one force-push in the life of the mirror. Turn Actions back on.
6. **Deploy key** (setup step 4), turn the private repo's Actions on, then run the
   `Mirror` workflow by hand (`gh workflow run mirror.yml -R <private>`). It must report
   every ref up to date. If it pushes anything, CI and the local run disagree — stop and
   investigate.
7. **Issues.** `gh label clone <public> -R <private>`, then transfer the agreed issues.
8. **Repoint.** The user's local clone: `git remote set-url origin <private-url>`.
   `docs/specs/spec.md` `repo:` and any issue-flow config → the private repo.
9. **Clean-up the user owns:** the PR body edits they chose, and the Support request.
   Hand them the draft; do not submit it for them.
10. **Verify.** The public repo's default branch shows no excluded path; its releases
    still list their assets; `check` passes on a fresh clone of the public repo.

#### Flip route

1. **Freeze**, as in the mirror route.
2. **Save the releases.** For each release: `gh release view <tag> -R <repo> --json
   name,body,isPrerelease,isDraft` and `gh release download <tag> -R <repo> -D
   <scratch>/releases/<tag>`. The new public repo has no releases until step 7.
3. **Rename and make private.** `gh repo rename <name>-private -R <owner>/<name>`, then
   `gh repo edit <owner>/<name>-private --visibility private
   --accept-visibility-change-consequences`. The issues, PRs, secrets, Actions history
   and settings stay with this repo. Turn its Actions off until step 6 — the install push
   would otherwise start a `Mirror` run with no public repo and no key.
4. **Install** the setup files on every mirrored branch of the private repo (setup steps
   2–3), with `cutover` = now. The publishing guards name the **new public** repo.
5. **Create the public repo** under the old name (`gh repo create <owner>/<name>
   --public`) with the old description, homepage and topics. This ends GitHub's redirect
   from the old name to the renamed repo, which is the intent. Turn its Actions off.
   Set again the secrets, variables, environments and rulesets from the Phase A list.
6. **Filter** a fresh `git clone --mirror` of the private repo with the pinned
   filter-repo, `check`, then push
   each mirrored branch and every tag to the public repo. It is empty, so this is a
   normal push, not a force-push. Turn the public repo's Actions on. Then the deploy key
   (setup step 4), the private repo's Actions on, and a manual `Mirror` run, which must
   report every ref up to date.
7. **Releases.** Recreate each saved release on its tag:
   `gh release create <tag> -R <public> --title <name> --notes-file <body> <assets>`
   (`--prerelease` where it was one). Mark the newest one latest.
8. **Integrations.** Repoint every outside integration from the Phase A list (deploy
   hosts, package trusted-publishing, Pages) to the new public repo. These are the
   user's accounts — give exact steps, and do what the CLI can do only with their go.
9. **Repoint** the local clone: `git remote set-url origin <private-url>`. Set
   `docs/specs/spec.md` `repo:` and any issue-flow config to the private repo.
10. **Verify.** `check` passes on a fresh clone of the public repo; the next tag publishes
    from the public repo; the site deploys; the old issue and PR URLs return 404 when
    you are signed out.

## Mode: import (outside contribution)

A PR opened on the public mirror:

1. `gh pr diff <n> -R <public> --patch > <scratch>/pr-<n>.patch`.
2. In the private repo, branch from the PR's target branch and `git am -3` the patch.
   `git am` keeps the contributor as the author. The patch never touches an excluded
   path, so it applies. Add the source to each commit:
   `git commit --amend --no-edit --trailer "Imported-from: <public>#<n>"` (for several
   commits, `git rebase --exec` the same command). Write the public PR fully qualified —
   a bare `#n` is rewritten to point at the private repo.
3. Open the PR in the private repo, with `<public>#<n>` in its title and body.
4. **Merge it with rebase** (`gh pr merge --rebase`), not squash. A squash merge makes
   the merger the author, demotes the contributor to a `Co-authored-by` trailer, and
   replaces the commit message — the trailer goes with it.
5. After the mirror syncs, comment on the public PR with the mirror commit that carries
   the change, thank the author, and close it (`gh pr close <n> -R <public> -c "..."`).

## Rules

- **Phase B never runs on the same turn as Phase A**, and never without the user saying
  go after reading the report.
- **One force-push, ever** — Phase B step 5. A later sync that is rejected means
  divergence. Find the cause; do not force. When the cause is found and fixed, and a
  `filter` of a fresh mirror clone of the private repo shows the correct refs, repair
  **only the refs that differ** with `--force`, with the public repo's Actions off, and
  with the user's go. A changed `exclude` list or `cutover` makes that every ref.
- **Tell the truth about what a rewrite cannot do:** forks, clones and archives keep the
  old history. The conversion stops further exposure. It does not recall anything.
