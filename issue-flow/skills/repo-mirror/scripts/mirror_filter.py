#!/usr/bin/env python3
"""Build the filtered history of a public mirror from a private source repo.

    python3 mirror_filter.py filter --config .github/mirror/config.json --source <repo> --out <dir>
    python3 mirror_filter.py check  --config .github/mirror/config.json --repo <dir>
    python3 mirror_filter.py stats  --config .github/mirror/config.json --source <repo>

`filter` copies the configured branches and every tag from <source> into a new
bare repo at <out>, then runs git filter-repo on it: the excluded paths leave
every commit, commits left empty are pruned, and in commits made after the
cutover a bare `#123` becomes `<private>#123`, so a mirror commit never links to
an unrelated public issue with the same number.

The output is deterministic. The same source history and the same config give
the same commit hashes every run, and a source that only gained new commits
gives a mirror that only gained new commits. That is what lets the sync job push
without --force: a rejected push means the mirror diverged, and the job must
fail rather than overwrite it. Pin the git-filter-repo version — a different
version can produce different hashes.

`check` exits 1 and lists every excluded path still reachable from any ref.
`stats` reports, without writing anything, which excluded paths the source
history holds.

The config file is JSON:

    {
      "private": "owner/repo-private",
      "public": "owner/repo",
      "branches": ["main"],
      "exclude": ["docs/specs/", "CLAUDE.md", "glob:*/CLAUDE.md"],
      "cutover": 1791331200
    }

An exclude entry is a path (a file, or a directory with or without a trailing
slash) or `glob:<pattern>`, where `*` also matches `/`, as in filter-repo.
`cutover` is a Unix time. Leave it out, or set it to 0, to rewrite no messages.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import tempfile


def run(args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.exit(f"error: {' '.join(args)}\n{result.stderr.strip()}")
    return result


def load_config(path):
    with open(path) as handle:
        config = json.load(handle)
    for key in ("private", "public", "branches", "exclude"):
        if key not in config:
            sys.exit(f"error: config {path} has no '{key}'")
    return config


def excluded(path, rules):
    for rule in rules:
        if rule.startswith("glob:"):
            if fnmatch.fnmatchcase(path, rule[len("glob:"):]):
                return True
        else:
            prefix = rule.rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
    return False


def history_paths(repo):
    """Every path that any commit reachable from any ref adds, changes or deletes."""
    out = run(["git", "log", "--all", "--format=", "--name-only", "--no-renames"], repo).stdout
    return {line for line in out.splitlines() if line}


def source_ref(source, branch):
    """The source ref that holds `branch`: a local branch, else origin's copy (a CI checkout)."""
    refs = run(["git", "ls-remote", source, f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]).stdout
    names = [line.split("\t")[1] for line in refs.splitlines()]
    for name in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        if name in names:
            return name
    sys.exit(f"error: the source has no branch '{branch}'")


def callback(config):
    cutover = config.get("cutover")
    if not cutover:
        return None
    private = config["private"].encode()
    # filter-repo runs this as a function body with `commit` in scope.
    return (
        "import re\n"
        f"if int(commit.committer_date.split()[0]) > {int(cutover)}:\n"
        f"    commit.message = re.sub(rb'(?<![\\w/#])#(\\d+)\\b', {private!r} + rb'#\\1', commit.message)\n"
    )


def do_filter(config, source, out):
    if os.path.exists(out) and os.listdir(out):
        sys.exit(f"error: {out} is not empty")
    run(["git", "init", "--quiet", "--bare", out])
    refspecs = [f"+{source_ref(source, b)}:refs/heads/{b}" for b in config["branches"]]
    refspecs.append("+refs/tags/*:refs/tags/*")
    run(["git", "fetch", "--quiet", "--no-tags", source, *refspecs], out)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        for rule in config["exclude"]:
            handle.write((rule.rstrip("/") if not rule.startswith("glob:") else rule) + "\n")
        paths_file = handle.name
    try:
        args = ["git", "filter-repo", "--force", "--quiet", "--invert-paths", "--paths-from-file", paths_file]
        body = callback(config)
        if body:
            args += ["--commit-callback", body]
        run(args, out)
    finally:
        os.unlink(paths_file)
    # filter-repo leaves a replace ref per rewritten commit; the mirror must not carry them.
    replace = run(["git", "for-each-ref", "--format=%(refname)", "refs/replace/"], out).stdout.split()
    for ref in replace:
        run(["git", "update-ref", "-d", ref], out)


def do_check(config, repo):
    leaks = sorted(p for p in history_paths(repo) if excluded(p, config["exclude"]))
    for path in leaks:
        print(f"LEAK {path}")
    if leaks:
        print(f"{len(leaks)} excluded path(s) are still in the history", file=sys.stderr)
        return 1
    print("clean: no excluded path is reachable from any ref")
    return 0


def do_stats(config, source):
    counts = {}
    for path in history_paths(source):
        for rule in config["exclude"]:
            if excluded(path, [rule]):
                counts[rule] = counts.get(rule, 0) + 1
                break
    for rule in config["exclude"]:
        print(f"{counts.get(rule, 0):6d}  {rule}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("filter", "check", "stats"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        if name == "filter":
            command.add_argument("--source", required=True)
            command.add_argument("--out", required=True)
        elif name == "check":
            command.add_argument("--repo", required=True)
        else:
            command.add_argument("--source", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "filter":
        do_filter(config, os.path.abspath(args.source), os.path.abspath(args.out))
        return 0
    if args.command == "check":
        return do_check(config, args.repo)
    return do_stats(config, args.source)


if __name__ == "__main__":
    sys.exit(main())
