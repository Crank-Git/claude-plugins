#!/usr/bin/env python3
"""Tests for issue-flow/skills/repo-mirror/scripts/mirror_filter.py.

    python3 scripts/test-mirror-filter.py

Builds a small real source repo with spec files, an issue reference before and
after the cutover, a tag, and a branch that is not mirrored. Checks that the
filter removes the excluded paths, rewrites only post-cutover references, gives
the same hashes on a second run, only appends when the source gains a commit,
and that `check` fails on an unfiltered repo. Needs git-filter-repo on PATH.
Never touches a network or the working repo.
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "skills", "repo-mirror", "scripts", "mirror_filter.py")

CUTOVER = 1_800_000_000
failures = []


def fail(message):
    failures.append(message)


def git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{date} +0000"
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def commit(repo, files, message, date):
    for path, text in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as handle:
            handle.write(text)
        git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message, date=date)


def mirror(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


def build(root):
    source = os.path.join(root, "source")
    os.makedirs(source)
    git(source, "init", "-q", "-b", "main")
    commit(source, {"main.go": "a\n", "docs/specs/spec.md": "secret\n", "CLAUDE.md": "c\n"}, "Add the core (#3)", CUTOVER - 100)
    commit(source, {"docs/specs/features/x.md": "secret\n"}, "Spec only", CUTOVER - 50)
    commit(source, {"internal/CLAUDE.md": "n\n", "main.go": "b\n"}, "Closes #12 and see owner/other#4", CUTOVER + 100)
    git(source, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    git(source, "switch", "-q", "-c", "issue/7-wip")
    commit(source, {"wip.go": "w\n"}, "WIP", CUTOVER + 200)
    git(source, "switch", "-q", "main")
    config = os.path.join(root, "config.json")
    with open(config, "w") as handle:
        json.dump({
            "private": "owner/repo-private",
            "public": "owner/repo",
            "branches": ["main"],
            "exclude": ["docs/specs/", "CLAUDE.md", "glob:*/CLAUDE.md"],
            "cutover": CUTOVER,
        }, handle)
    return source, config


def run_filter(config, source, out):
    result = mirror("filter", "--config", config, "--source", source, "--out", out)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def main():
    with tempfile.TemporaryDirectory() as root:
        source, config = build(root)
        first = os.path.join(root, "first")
        run_filter(config, source, first)

        files = set(git(first, "log", "--all", "--format=", "--name-only").split())
        if files != {"main.go"}:
            fail(f"filtered history should hold only main.go, holds {sorted(files)}")
        if git(first, "rev-list", "--count", "main") != "2":
            fail("the spec-only commit should be pruned, leaving 2 commits")
        if git(first, "for-each-ref", "--format=%(refname)", "refs/heads/") != "refs/heads/main":
            fail("only the configured branch should be mirrored")
        if git(first, "tag") != "v1.0.0":
            fail("the tag should be mirrored")
        if git(first, "for-each-ref", "refs/replace/"):
            fail("replace refs should be removed")

        messages = git(first, "log", "--format=%s", "main").splitlines()
        if messages[0] != "Closes owner/repo-private#12 and see owner/other#4":
            fail(f"post-cutover reference not rewritten as expected: {messages[0]!r}")
        if messages[1] != "Add the core (#3)":
            fail(f"pre-cutover reference must stay unchanged: {messages[1]!r}")

        check = mirror("check", "--config", config, "--repo", first)
        if check.returncode != 0:
            fail(f"check should pass on the filtered repo: {check.stdout}")
        check = mirror("check", "--config", config, "--repo", source)
        if check.returncode != 1 or "LEAK internal/CLAUDE.md" not in check.stdout:
            fail(f"check should fail on the source and name the nested CLAUDE.md: {check.stdout}")

        second = os.path.join(root, "second")
        run_filter(config, source, second)
        if git(first, "rev-parse", "main", "v1.0.0") != git(second, "rev-parse", "main", "v1.0.0"):
            fail("two runs on the same source should give the same hashes")

        old_head = git(first, "rev-parse", "main")
        commit(source, {"main.go": "c\n", "docs/specs/spec.md": "more\n"}, "Next", CUTOVER + 300)
        third = os.path.join(root, "third")
        run_filter(config, source, third)
        if git(third, "rev-parse", "main~1") != old_head:
            fail("a new source commit should append to the mirror, not rewrite it")

        stats = mirror("stats", "--config", config, "--source", source)
        if "     2  docs/specs/" not in stats.stdout:
            fail(f"stats should count 2 spec paths: {stats.stdout}")

    for message in failures:
        print(f"FAIL {message}")
    if failures:
        return 1
    print("ok: mirror_filter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
