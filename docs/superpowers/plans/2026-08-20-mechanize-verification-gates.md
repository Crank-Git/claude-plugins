# Mechanize A/D/F Verification Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three prose-only trust points in issue-flow — locate/claim existence checks, coordination-state swaps, and CI-status trust — with small, git-native, independently testable scripts, and wire the skill's docs to require them at the exact points where measured failures already happened.

**Architecture:** Three standalone Python scripts under `issue-flow/scripts/`, each with a paired `scripts/test-<name>.py` at the repo root (matching the existing `peers.py` / `test-peers.py` convention: plain module import via `importlib`, `failures = []` + `fail()`, exit 1 on any failure). Two of the three (`verify_claim.py`, `state_cas.py`) operate purely on local git plumbing and are fully testable against temp/bare repos with no network. The third (`verify_ci_ran.py`) separates a pure decision function (`decide_ran`, fixture-testable) from forge-specific fetch code (`gh` CLI / Gitea REST, exercised only at the integration-test level, not unit level). Wiring is prose edits to `SKILL.md`, `references/parallelism.md`, and `references/batching.md` at the specific line ranges that already document each gap.

**Tech Stack:** Python 3 (stdlib only — `argparse`, `json`, `subprocess`, `urllib.request` — no new dependencies), git plumbing (`hash-object`, `mktree`, `commit-tree`, `update-ref`), bash for manual verification.

**Spec:** No separate spec doc — this plan implements the A/D/F design agreed in conversation (see git commit message for context); the "spec" is the measured-failure prose already in `SKILL.md:389,507,680-704` and `references/parallelism.md:99-144`.

## Global Constraints

- No pytest, no new dependencies — match `scripts/test-peers.py`'s plain-assertion style exactly.
- Every new script gets a docstring citing the measured failure it mechanizes (repo convention — see `peers.py`'s header).
- Scripts must work standalone via `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py"` — no import of issue-flow-internal modules beyond stdlib.
- Doc edits are prose insertions at existing sections — do not restructure or reflow surrounding prose beyond what's needed to add the reference.
- Nonzero exit code is always the "do not trust this without investigating" signal across all three scripts — keep that convention consistent.

---

### Task 1: Branch prep

**Files:** none (git operations only)

- [ ] **Step 1: Align local `dev` with `main`**

`dev` has zero commits not on `main` (confirmed: `git log --oneline main..dev` is empty) and is 7 commits behind. Rebase it onto `main` locally — this will not touch `origin/dev`:

```bash
git checkout dev
git rebase main
```

Expect: rebase completes with no conflicts (trees are already identical — `git diff origin/dev main` is empty), `git log --oneline -1 dev` now shows `main`'s tip commit.

- [ ] **Step 2: Create the feature branch off the rebased `dev`**

```bash
git checkout -b issue-flow/mechanize-verification-gates dev
```

- [ ] **Step 3: Confirm branch base**

```bash
git log --oneline -1
```

Expect: same SHA as `main`'s current tip (`2501af5` or later if main has moved).

---

### Task 2: `verify_claim.py` (A — mechanize locate/existence claims)

**Files:**
- Create: `issue-flow/scripts/verify_claim.py`
- Test: `scripts/test-verify-claim.py`

**Interfaces:**
- Produces: CLI `verify_claim.py --repo PATH --ref REF --pattern PATTERN [--expect-sha SHA] [--json]`. Exit 0 with JSON `{"ref","resolved_sha","pattern","found","matches","stale":false}` when the check ran cleanly (found is the actual answer). Exit 2 on bad ref, exit 3 when `--expect-sha` is given and doesn't match resolved sha (JSON `{"stale":true,...}`), exit 4 on `git grep` error.

- [ ] **Step 1: Write `issue-flow/scripts/verify_claim.py`**

```python
#!/usr/bin/env python3
"""Mechanically verify an existence claim about code at a git ref.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo . --ref origin/main --pattern "def foo"
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo . --ref origin/main --pattern "def foo" --expect-sha abc123 --json

A locate-pass or review agent's "this does not exist" is only as good as the
ref it read. Two measured failures (see SKILL.md's locate-pass section) were
both a truthful report against a stale or wrong ref: main instead of the
batch's integration branch, or a working tree one merge behind origin. This
does the same check with `git grep` against a pinned ref, so a claim about
existence/absence is checked against ground truth instead of trusted as
prose. Exit code is authoritative: 0 means the JSON result is trustworthy;
any nonzero means don't trust the claim without investigating.
"""

import argparse
import json
import subprocess
import sys


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def resolve_sha(repo, ref):
    result = run(["git", "rev-parse", "--verify", ref], repo)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def grep(repo, sha, pattern):
    result = run(["git", "grep", "-n", "-I", "-e", pattern, sha], repo)
    if result.returncode not in (0, 1):
        return None, result.stderr.strip()
    matches = [line for line in result.stdout.splitlines() if line.strip()]
    return matches, None


def check(repo, ref, pattern, expect_sha):
    sha = resolve_sha(repo, ref)
    if sha is None:
        return 2, {"error": f"cannot resolve ref {ref!r} in {repo!r}"}

    if expect_sha and sha != expect_sha:
        return 3, {"ref": ref, "resolved_sha": sha, "expected_sha": expect_sha, "stale": True}

    matches, err = grep(repo, sha, pattern)
    if err is not None:
        return 4, {"error": f"git grep failed: {err}"}

    return 0, {
        "ref": ref,
        "resolved_sha": sha,
        "pattern": pattern,
        "found": bool(matches),
        "matches": matches,
        "stale": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--expect-sha", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    code, payload = check(args.repo, args.ref, args.pattern, args.expect_sha)

    if args.json or "error" in payload:
        print(json.dumps(payload), file=sys.stderr if "error" in payload else sys.stdout)
    elif payload.get("stale"):
        print(f"STALE: {payload['ref']} resolved to {payload['resolved_sha']}, expected {payload['expected_sha']}")
    elif payload["found"]:
        print(f"FOUND at {payload['resolved_sha']}:")
        for line in payload["matches"]:
            print(f"  {line}")
    else:
        print(f"NOT FOUND at {payload['resolved_sha']} — claim of absence is supported")

    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `scripts/test-verify-claim.py`**

```python
#!/usr/bin/env python3
"""Tests for issue-flow/scripts/verify_claim.py.

    python3 scripts/test-verify-claim.py

Builds a tiny real git repo with two commits (a symbol added in the second)
and checks: found, not-found, stale-ref detection, and bad-ref handling.
Never touches a real network or the working repo.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "verify_claim", os.path.join(ROOT, "issue-flow", "scripts", "verify_claim.py")
)
verify_claim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_claim)

failures = []


def fail(message):
    failures.append(message)


def git(repo, *args, env=None):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def build_repo(root):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("print('hello')\n")
    git(repo, "add", "a.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first", env=env)
    first_sha = git(repo, "rev-parse", "HEAD")

    with open(os.path.join(repo, "b.py"), "w") as handle:
        handle.write("def new_helper():\n    return 1\n")
    git(repo, "add", "b.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "second", env=env)
    second_sha = git(repo, "rev-parse", "HEAD")
    return repo, first_sha, second_sha


work = tempfile.mkdtemp(prefix="verify-claim-test-")
try:
    repo, first_sha, second_sha = build_repo(work)

    code, payload = verify_claim.check(repo, "HEAD", "def new_helper", None)
    if code != 0 or not payload["found"]:
        fail(f"expected found=True at HEAD, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, first_sha, "def new_helper", None)
    if code != 0 or payload["found"]:
        fail(f"expected found=False at first commit, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, first_sha, "def new_helper", second_sha)
    if code != 3 or not payload.get("stale"):
        fail(f"expected stale detection when ref != expect-sha, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, "does-not-exist-ref", "def new_helper", None)
    if code != 2:
        fail(f"expected code=2 for unresolvable ref, got code={code} payload={payload}")
finally:
    subprocess.run(["rm", "-rf", work])

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("verify_claim: all cases pass")
```

- [ ] **Step 3: Run the test and confirm it passes**

```bash
python3 scripts/test-verify-claim.py
```

Expected: `verify_claim: all cases pass`

- [ ] **Step 4: Commit**

```bash
git add issue-flow/scripts/verify_claim.py scripts/test-verify-claim.py
git commit -m "issue-flow: mechanize existence-claim verification (A)"
```

---

### Task 3: `state_cas.py` (F — git-native compare-and-swap for coordination state)

**Files:**
- Create: `issue-flow/scripts/state_cas.py`
- Test: `scripts/test-state-cas.py`

**Interfaces:**
- Produces: CLI `state_cas.py get --repo PATH --remote NAME --key KEY` (prints `{"key","value"}`, `value` is `null` if absent) and `state_cas.py set --repo PATH --remote NAME --key KEY --expect JSON|absent --value JSON`. `set` exits 0 + `{"ok":true,...}` on success, 2 + `{"ok":false,"reason":"stale","current":...}` on expect mismatch, 3 + `{"ok":false,"reason":"race-lost",...}` on a lost push race.
- State lives at `refs/issue-flow/state/<key>`, one orphan-chained commit per write, each with a single `state.json` blob — no working-tree checkout needed to read or write.

- [ ] **Step 1: Write `issue-flow/scripts/state_cas.py`**

```python
#!/usr/bin/env python3
"""Git-native compare-and-swap for small coordination state (issue claims,
batch-status swaps) — no external lock service, no server.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" get --repo . --remote origin --key batch-42
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key batch-42 \
        --expect '{"status":"open"}' --value '{"status":"merging"}'
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key issue-17 \
        --expect absent --value '{"owner":"worker-a"}'

State lives as an orphan commit chain under refs/issue-flow/state/<key> (one
commit per write, each holding a single state.json blob — no working-tree
checkout needed). git's own non-fast-forward push rejection is the race
arbiter across concurrent workers/machines: `set` fetches the current value,
refuses to proceed if it doesn't match --expect, and if the push is rejected
because someone else wrote first, that's a lost race, not a partial write.

This replaces the "re-read the issue's labels and assignees, hope nobody
raced you" pattern (references/parallelism.md's Claim race section) and the
self-checking batch-swap gate (SKILL.md's batch-swap section) with a check
whose correctness is a property of git's push return value, not an agent's
belief that it did the swap.
"""

import argparse
import json
import subprocess
import sys


def run(args, cwd, input_bytes=None, check=False):
    result = subprocess.run(args, cwd=cwd, input=input_bytes, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.decode()}")
    return result


def ref_name(key):
    return f"refs/issue-flow/state/{key}"


def fetch_current(repo, remote, key):
    """Returns (commit_sha_or_None, value_dict_or_None)."""
    result = run(["git", "fetch", remote, ref_name(key)], repo)
    if result.returncode != 0:
        stderr = result.stderr.decode()
        if "couldn't find remote ref" in stderr or "not found" in stderr:
            return None, None
        raise RuntimeError(f"fetch failed: {stderr.strip()}")
    fetch_head = run(["git", "rev-parse", "FETCH_HEAD"], repo, check=True)
    commit_sha = fetch_head.stdout.decode().strip()
    blob = run(["git", "cat-file", "-p", f"{commit_sha}:state.json"], repo, check=True)
    return commit_sha, json.loads(blob.stdout.decode())


def write_commit(repo, key, value, parent_sha):
    blob = run(["git", "hash-object", "-w", "--stdin"], repo, input_bytes=json.dumps(value).encode(), check=True)
    blob_sha = blob.stdout.decode().strip()
    tree_input = f"100644 blob {blob_sha}\tstate.json\n".encode()
    tree = run(["git", "mktree"], repo, input_bytes=tree_input, check=True)
    tree_sha = tree.stdout.decode().strip()
    commit_args = ["git", "commit-tree", tree_sha, "-m", f"state: {key}"]
    if parent_sha:
        commit_args += ["-p", parent_sha]
    commit = run(commit_args, repo, check=True)
    return commit.stdout.decode().strip()


def cmd_get(args):
    _, value = fetch_current(args.repo, args.remote, args.key)
    print(json.dumps({"key": args.key, "value": value}))
    return 0


def cmd_set(args):
    parent_sha, current = fetch_current(args.repo, args.remote, args.key)
    expect = None if args.expect == "absent" else json.loads(args.expect)
    if current != expect:
        print(json.dumps({"ok": False, "reason": "stale", "current": current}))
        return 2

    new_value = json.loads(args.value)
    new_commit = write_commit(args.repo, args.key, new_value, parent_sha)
    run(["git", "update-ref", ref_name(args.key), new_commit], args.repo, check=True)

    push_spec = f"{ref_name(args.key)}:{ref_name(args.key)}"
    push = run(["git", "push", args.remote, push_spec], args.repo)
    if push.returncode != 0:
        print(json.dumps({"ok": False, "reason": "race-lost", "detail": push.stderr.decode().strip()}))
        return 3

    print(json.dumps({"ok": True, "key": args.key, "value": new_value}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    sub = parser.add_subparsers(dest="command", required=True)

    get_p = sub.add_parser("get")
    get_p.add_argument("--key", required=True)

    set_p = sub.add_parser("set")
    set_p.add_argument("--key", required=True)
    set_p.add_argument("--expect", required=True, help="JSON of expected current value, or 'absent'")
    set_p.add_argument("--value", required=True, help="JSON of new value")

    args = parser.parse_args()
    try:
        return cmd_get(args) if args.command == "get" else cmd_set(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `scripts/test-state-cas.py`**

```python
#!/usr/bin/env python3
"""Tests for issue-flow/scripts/state_cas.py.

    python3 scripts/test-state-cas.py

Uses a local bare repo as "origin" (no real network) and a local clone as the
worker, to exercise: create-when-absent, CAS success, stale-expect rejection,
and a genuine lost-race (two clones racing the same key).
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "state_cas", os.path.join(ROOT, "issue-flow", "scripts", "state_cas.py")
)
state_cas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_cas)

failures = []


def fail(message):
    failures.append(message)


def git(repo, *args):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


work = tempfile.mkdtemp(prefix="state-cas-test-")
try:
    bare = os.path.join(work, "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)

    clone_a = os.path.join(work, "a")
    clone_b = os.path.join(work, "b")
    subprocess.run(["git", "clone", "-q", bare, clone_a], check=True)
    subprocess.run(["git", "clone", "-q", bare, clone_b], check=True)

    # 1. get on an absent key returns null, no error.
    parent, current = state_cas.fetch_current(clone_a, "origin", "issue-17")
    if current is not None or parent is not None:
        fail(f"expected absent key to read as None, got parent={parent} current={current}")

    # 2. first set (expect absent) succeeds.
    class Args:
        pass

    args = Args()
    args.repo, args.remote, args.key = clone_a, "origin", "issue-17"
    args.expect, args.value = "absent", json.dumps({"owner": "worker-a"})
    code = state_cas.cmd_set(args)
    if code != 0:
        fail(f"expected first set to succeed, got code={code}")

    # 3. a stale expect on the same clone is rejected.
    args.expect, args.value = "absent", json.dumps({"owner": "worker-x"})
    code = state_cas.cmd_set(args)
    if code != 2:
        fail(f"expected stale-expect rejection, got code={code}")

    # 4. a genuine race: clone_b writes first, clone_a's push (based on a now-stale parent) loses.
    args_b = Args()
    args_b.repo, args_b.remote, args_b.key = clone_b, "origin", "race-key"
    args_b.expect, args_b.value = "absent", json.dumps({"owner": "worker-b"})
    if state_cas.cmd_set(args_b) != 0:
        fail("expected worker-b's first write on race-key to succeed")

    # clone_a still thinks race-key is absent (never fetched b's write) and tries the same create.
    args_a = Args()
    args_a.repo, args_a.remote, args_a.key = clone_a, "origin", "race-key"
    args_a.expect, args_a.value = "absent", json.dumps({"owner": "worker-a"})
    code = state_cas.cmd_set(args_a)
    if code != 2:
        fail(f"expected worker-a to see worker-b's value on refetch and reject as stale, got code={code}")

    # 5. correct CAS sequence (read-then-write with the real current value) succeeds.
    parent, current = state_cas.fetch_current(clone_a, "origin", "race-key")
    args_a.expect, args_a.value = json.dumps(current), json.dumps({"owner": "worker-a", "took_over": True})
    code = state_cas.cmd_set(args_a)
    if code != 0:
        fail(f"expected correctly-sequenced CAS to succeed, got code={code}")
finally:
    subprocess.run(["rm", "-rf", work])

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("state_cas: all cases pass")
```

- [ ] **Step 3: Run the test and confirm it passes**

```bash
python3 scripts/test-state-cas.py
```

Expected: `state_cas: all cases pass`. If step 4/5's race scenario doesn't reproduce a stale rejection, re-check that `cmd_set` always calls `fetch_current` fresh (not cached) — that's the actual CAS guarantee under test.

- [ ] **Step 4: Commit**

```bash
git add issue-flow/scripts/state_cas.py scripts/test-state-cas.py
git commit -m "issue-flow: git-native compare-and-swap for coordination state (F)"
```

---

### Task 4: `verify_ci_ran.py` (D — mechanize CI-execution trust)

**Files:**
- Create: `issue-flow/scripts/verify_ci_ran.py`
- Test: `scripts/test-verify-ci-ran.py`

**Interfaces:**
- Produces: pure function `decide_ran(runs_for_sha: list[dict]) -> dict` — each run dict is `{"id","status","log_bytes"}`; returns `{"ran": bool, "reason": str, ...}`. CLI wraps it with `--forge github|gitea` (network fetch) or `--fixture PATH` (canned JSON, no network — this is what the test uses).

- [ ] **Step 1: Write `issue-flow/scripts/verify_ci_ran.py`**

```python
#!/usr/bin/env python3
"""Decide whether CI actually executed for a commit SHA — not just whether a
status exists.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge github --repo owner/name --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge gitea --gitea-url https://gitea.example \
        --owner o --repo r --gitea-token "$TOKEN" --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --fixture runs.json --sha SHA   # test/dry-run

A red or green check can be reported with zero jobs having ever run — a
disabled runner, an unparseable workflow (Gitea registers no run at all for
these), or a skip-ci token reintroduced by a squash-merge commit body nobody
typed (see references/forge.md's Actions and CI section). `decide_ran` is the
mechanized version of "does retrievable log output exist for this SHA,"
independent of whatever the status field claims — kept pure and
fixture-testable, separate from the forge-specific fetch below it.
"""

import argparse
import json
import subprocess
import sys
import urllib.request


def decide_ran(runs_for_sha):
    if not runs_for_sha:
        return {"ran": False, "reason": "no run recorded for this SHA"}
    with_logs = [r for r in runs_for_sha if r.get("log_bytes", 0) > 0]
    if not with_logs:
        return {"ran": False, "reason": "run(s) recorded but zero log bytes retrievable — status is not evidence"}
    return {"ran": True, "reason": f"{len(with_logs)} run(s) with retrievable logs", "runs": with_logs}


def fetch_github(repo, sha):
    result = subprocess.run(
        ["gh", "run", "list", "--repo", repo, "--commit", sha, "--json", "databaseId,status"],
        capture_output=True, text=True, check=True,
    )
    runs = json.loads(result.stdout)
    out = []
    for run in runs:
        log = subprocess.run(
            ["gh", "run", "view", str(run["databaseId"]), "--repo", repo, "--log"],
            capture_output=True, text=True,
        )
        out.append({"id": run["databaseId"], "status": run.get("status"), "log_bytes": len(log.stdout)})
    return out


def fetch_gitea(base_url, owner, repo, token, sha):
    req = urllib.request.Request(
        f"{base_url}/api/v1/repos/{owner}/{repo}/commits/{sha}/status",
        headers={"Authorization": f"token {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        payload = json.loads(resp.read())
    statuses = payload.get("statuses", payload if isinstance(payload, list) else [])
    out = []
    for status in statuses:
        target = status.get("target_url", "")
        log_bytes = 0
        if target:
            try:
                with urllib.request.urlopen(target) as log_resp:
                    log_bytes = len(log_resp.read())
            except Exception:
                log_bytes = 0
        out.append({"id": status.get("id"), "status": status.get("status"), "log_bytes": log_bytes})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--forge", choices=["github", "gitea"])
    parser.add_argument("--repo")
    parser.add_argument("--owner")
    parser.add_argument("--gitea-url")
    parser.add_argument("--gitea-token")
    parser.add_argument("--fixture")
    args = parser.parse_args()

    if args.fixture:
        with open(args.fixture) as handle:
            runs = json.load(handle)
    elif args.forge == "github":
        runs = fetch_github(args.repo, args.sha)
    elif args.forge == "gitea":
        runs = fetch_gitea(args.gitea_url, args.owner, args.repo, args.gitea_token, args.sha)
    else:
        print("error: specify --forge or --fixture", file=sys.stderr)
        return 2

    result = decide_ran(runs)
    print(json.dumps(result))
    return 0 if result["ran"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `scripts/test-verify-ci-ran.py`**

```python
#!/usr/bin/env python3
"""Tests for issue-flow/scripts/verify_ci_ran.py's decide_ran (pure, no network)."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "verify_ci_ran", os.path.join(ROOT, "issue-flow", "scripts", "verify_ci_ran.py")
)
verify_ci_ran = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_ci_ran)

failures = []


def fail(message):
    failures.append(message)


result = verify_ci_ran.decide_ran([])
if result["ran"]:
    fail(f"no runs at all must be ran=False, got {result}")

result = verify_ci_ran.decide_ran([{"id": 1, "status": "completed", "log_bytes": 0}])
if result["ran"]:
    fail(f"a run with zero log bytes must be ran=False (status alone is not evidence), got {result}")

result = verify_ci_ran.decide_ran([{"id": 1, "status": "completed", "log_bytes": 5000}])
if not result["ran"]:
    fail(f"a run with real log bytes must be ran=True, got {result}")

result = verify_ci_ran.decide_ran([
    {"id": 1, "status": "completed", "log_bytes": 0},
    {"id": 2, "status": "completed", "log_bytes": 800},
])
if not result["ran"] or len(result.get("runs", [])) != 1:
    fail(f"mixed runs must report ran=True with only the log-bearing run counted, got {result}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("verify_ci_ran: all cases pass")
```

- [ ] **Step 3: Run the test and confirm it passes**

```bash
python3 scripts/test-verify-ci-ran.py
```

Expected: `verify_ci_ran: all cases pass`

- [ ] **Step 4: Commit**

```bash
git add issue-flow/scripts/verify_ci_ran.py scripts/test-verify-ci-ran.py
git commit -m "issue-flow: mechanize CI-execution trust (D)"
```

---

### Task 5: Wire the docs to require these scripts at the measured-failure points

**Files:**
- Modify: `issue-flow/skills/issue-flow/SKILL.md` (locate-pass section near line 507; CI section near lines 680-704)
- Modify: `issue-flow/skills/issue-flow/references/parallelism.md` (claim-race section line 99-105; binary-diff section line 130-144)
- Modify: `issue-flow/skills/issue-flow/references/batching.md` (sub-merge/batch gate checklists, lines ~172-263)
- Modify: `issue-flow/references/forge.md` (Actions and CI section, line ~251)

- [ ] **Step 1: `parallelism.md` — claim race.** After the existing paragraph at line 105 ("Assign `@me` as part of the claim so the assignee acts as the lock signal."), add:

```markdown

For coordination that needs a harder guarantee than re-read-and-hope (a batch
status swap, an issue claim across machines), use the git-native CAS helper
instead of trusting a re-read:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin \
      --key issue-<n> --expect absent --value '{"owner":"<worker-id>"}'

A nonzero exit means either someone already holds it (`reason: stale`) or a
push race was lost (`reason: race-lost`) — either way, abandon and pick the
next issue rather than retrying blind.
```

- [ ] **Step 2: `parallelism.md` — binary-diff blind spot.** After the paragraph ending "...and say in the review which files were handled that way." (around line 140), add:

```markdown

Detect this mechanically instead of relying on a reviewer to notice: `git
diff --numstat <base> <head>` reports `-\t-` for any file git treats as
binary, and any file with a byte delta but no line-count delta is the
oversized-diff case. Route those paths to a direct-read step before the
review agent ever sees the PR diff, rather than trusting it to catch the gap.
```

- [ ] **Step 3: `SKILL.md` — locate-pass section.** Find the paragraph beginning "Both failures were measured in live runs" (around line 507) and add immediately after it:

```markdown

Treat any locate-pass "this does not exist" as a claim to verify, not a fact
to accept: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo .
--ref <the exact ref the agent was told to read> --pattern <symbol> --json`.
A nonzero exit (bad ref, or `stale: true` when checked against the batch's
actual current head) means the claim's input was wrong — investigate before
believing the "does not exist."
```

- [ ] **Step 4: `SKILL.md` — CI section.** Find the paragraph beginning "CI exists but produced no usable result" (around line 704) and add after it:

```markdown

Mechanize the "did it actually run" question rather than inferring it from
prose: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge
<github|gitea> --repo <owner/name> --sha <head_sha>` exits 0 only when
retrievable log bytes exist for that SHA — a status with zero log bytes is
treated the same as no run at all, regardless of what the status field says.
```

- [ ] **Step 5: `references/forge.md` — Actions and CI section.** Add a subsection after the existing CI-status material (around line 251+) documenting `verify_ci_ran.py` as the canonical "did this SHA's CI actually execute" check, cross-referencing the SKILL.md wiring from Step 4 rather than duplicating the explanation.

```markdown

### Verifying CI actually executed

A green or red check can exist with zero jobs having run — see the measured
failure modes above. `issue-flow/scripts/verify_ci_ran.py` mechanizes the
check: it fetches runs for a SHA (via `gh` for GitHub, the Actions REST API
for Gitea) and requires retrievable log bytes, not just a status field,
before reporting `ran: true`. Use it at the point SKILL.md's CI section
requires it — before trusting any check as evidence the commit was tested.
```

- [ ] **Step 6: `batching.md` — sub-merge/batch gate checklists.** Read the "Sub-merge gate checklist" (line 172) and "Batch gate checklist" (line 213) in full, then add one line to each pointing at `state_cas.py` for the actual status-label swap step, replacing implicit trust in "the PM did the swap" with an explicit CAS call plus its verified exit code.

- [ ] **Step 7: Read the full diff of all doc edits and confirm no other prose in these files now contradicts the new mechanized steps** (e.g., don't leave an old "just re-read and proceed" instruction standing next to the new hard requirement).

- [ ] **Step 8: Commit**

```bash
git add issue-flow/skills/issue-flow/SKILL.md issue-flow/skills/issue-flow/references/parallelism.md \
        issue-flow/skills/issue-flow/references/batching.md issue-flow/references/forge.md
git commit -m "issue-flow: wire A/D/F scripts into the skill at their measured-failure points"
```

---

### Task 6: Wire into CI, full local verification, merge check

**Files:**
- Modify: `.github/workflows/validate.yml`

- [ ] **Step 1: Add the three new test steps to `validate.yml`**, matching the existing pattern (insert after the "Test the preflight hook" step, before "Test the spec renderer" or at the end — match existing ordering style):

```yaml
      - name: Test the claim verifier
        run: python3 scripts/test-verify-claim.py

      - name: Test the state CAS helper
        run: python3 scripts/test-state-cas.py

      - name: Test the CI-execution verifier
        run: python3 scripts/test-verify-ci-ran.py
```

- [ ] **Step 2: Run the full existing test suite plus the new tests, in order, and confirm every one passes**

```bash
python3 scripts/test-validate.py
python3 scripts/test-peers.py
python3 scripts/test-guard-spawn.py
python3 scripts/test-preflight.py
python3 scripts/test-render-spec.py
python3 scripts/test-verify-claim.py
python3 scripts/test-state-cas.py
python3 scripts/test-verify-ci-ran.py
python3 scripts/validate-manifests.py
```

Expected: every command exits 0. `validate-manifests.py` in particular must still pass after the `SKILL.md`/`references/*.md` edits — it checks markdown cross-links and frontmatter; a broken relative link in the new wiring text would fail it.

- [ ] **Step 3: Confirm the branch merges cleanly into `main` without actually merging yet**

```bash
git fetch origin main
git merge-tree "$(git merge-base HEAD origin/main)" HEAD origin/main
```

Expected: no `<<<<<<<` conflict markers in the output. If there are conflicts, resolve on the feature branch and re-run this check — do not merge with unresolved conflicts.

- [ ] **Step 4: Commit the workflow change**

```bash
git add .github/workflows/validate.yml
git commit -m "issue-flow: run the new verification-gate tests in CI"
```

---

### Task 7: Push and open the PR

**Files:** none (git/forge operations only)

- [ ] **Step 1: Push the branch to `origin`**

```bash
git push -u origin issue-flow/mechanize-verification-gates
```

- [ ] **Step 2: Open a PR against `main`** with a body summarizing: what A/D/F are, the three scripts, where they're wired in, and the exact verification commands run in Task 6 Step 2 — this is the "heavy verification" record, not a separate step.

```bash
gh pr create --base main --head issue-flow/mechanize-verification-gates \
  --title "issue-flow: mechanize A/D/F verification gates (claim, CAS, CI-execution)" \
  --body "$(cat <<'EOF'
## Summary
- verify_claim.py — mechanically checks an existence claim against a pinned
  git ref instead of trusting an agent's prose (parallelism.md's measured
  stale-ref failures).
- state_cas.py — git-native compare-and-swap for coordination state (issue
  claims, batch-status swaps), using non-fast-forward push rejection as the
  race arbiter instead of "re-read and hope."
- verify_ci_ran.py — requires retrievable log bytes for a SHA before
  trusting a CI status, closing the "red/green with zero jobs run" gap.
- Wired into SKILL.md, parallelism.md, batching.md, and forge.md at the
  exact points that already document each gap.

## Test plan
- [x] python3 scripts/test-verify-claim.py
- [x] python3 scripts/test-state-cas.py
- [x] python3 scripts/test-verify-ci-ran.py
- [x] Full existing suite (test-validate, test-peers, test-guard-spawn, test-preflight, test-render-spec)
- [x] python3 scripts/validate-manifests.py
- [x] git merge-tree clean against main (no conflicts)
- [x] New tests wired into .github/workflows/validate.yml — remote CI will run them on this PR
EOF
)"
```

- [ ] **Step 3: Wait for `validate.yml` to run on the PR and confirm it's green** (this is remote CI actually exercising the new steps — the local runs in Task 6 are necessary but not sufficient).

- [ ] **Step 4: Post the verification summary as a PR comment** (separate from the PR body, per the user's "comment AFTER heavy verification" instruction) once remote CI is confirmed green — reference the specific run.

```bash
gh pr comment --body "Heavy verification complete: all local test suites pass, validate-manifests clean, merge-tree against main conflict-free, and remote CI (validate.yml) green on this PR — see the Checks tab."
```

- [ ] **Step 5: Do not merge.** Leave the PR open for review — merging into `main` is a separate decision for the repo owner, not part of this plan.

---

## Self-Review Notes

- **Spec coverage:** A → Task 2. D → Task 4 (+ Task 5 Step 4-5 wiring). F → Task 3 (+ Task 5 Step 1, 6 wiring). All three wired at the exact SKILL.md/reference lines identified during research — no gap.
- **Placeholder scan:** no TBD/TODO; all code blocks are complete, runnable scripts, not sketches.
- **Type/interface consistency:** `verify_claim.check()` returns `(code, payload)` used identically by the CLI and the test. `state_cas.fetch_current()` / `cmd_set()` signatures match between implementation and test. `verify_ci_ran.decide_ran()` takes the same `runs_for_sha` shape in both the fetch functions and the test fixtures.
