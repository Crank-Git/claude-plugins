#!/usr/bin/env python3
"""Tests for the checks in validate-manifests.py.

    python3 scripts/test-validate.py

The spawn lint decides whether a documented `Agent` spawn strands the PM, and it
has to tell an `Agent` call apart from a `Bash` call that legitimately passes
`run_in_background`. Both directions are load-bearing: a missed Agent site
re-introduces issue #25, and a false positive on a Bash site would push someone
to delete a parameter that is correct there. Every case below is taken from real
text in this repository or from the probes in that issue.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "validate_manifests", os.path.join(ROOT, "scripts", "validate-manifests.py")
)
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)

failures = []


def check(name, text, expect_names=0, expect_bg=0):
    found = validate.spawn_lint(text)
    names = sum(1 for _, message in found if "no `isolation:`" in message)
    backgrounds = sum(1 for _, message in found if "run_in_background" in message)
    if names != expect_names or backgrounds != expect_bg:
        failures.append(
            f"{name}: expected {expect_names} name / {expect_bg} background finding(s), "
            f"got {names} / {backgrounds} — {[m for _, m in found]}"
        )


# --- the shape that caused issue #25: named, un-isolated ---------------------
check(
    "named spawn without isolation is flagged",
    'Spawn with `Agent`, `agentType: "issue-flow:issue-worker"`, `name: "worker-<n>"`.',
    expect_names=1,
)
check(
    "named spawn with isolation is clean",
    'Spawn with `Agent`, `agentType: "x"`, **`isolation: "worktree"`**, `name: "worker-<n>"`.',
)
check(
    "unnamed spawn is clean",
    'Fan out `Agent` calls with `subagent_type: "Explore"` and take back a summary.',
)
check(
    "prose about a name outside a spawn spec is not a spawn spec",
    "The `name:` on that issue label is unrelated to agents.",
)

# --- run_in_background: inert on Agent, correct on Bash ----------------------
check(
    "run_in_background on an agentType spawn is flagged",
    'Spawn `agentType: "issue-flow:deploy-watcher"`, `run_in_background: true`, Haiku.',
    expect_bg=1,
)
check(
    "run_in_background beside isolation is flagged",
    '- `run_in_background: true` and `isolation: "worktree"` compose — both take.',
    expect_bg=1,
)
check(
    "run_in_background on a Bash call is clean",
    "**Launch that call with `run_in_background: true`.** One blocking call is the "
    "right shape, and the `Bash` timeout ceiling does not apply.",
)
check(
    "run_in_background in a shell-loop paragraph is clean",
    "`pollSeconds` is the `sleep` interval *inside* a shell loop launched with "
    "`run_in_background: true`.",
)

# --- both failures in one paragraph, and the suppression escape --------------
check(
    "a paragraph can carry both findings",
    'Spawn with `Agent`, `subagent_type: "general-purpose"`, `name: "reviewer"`, '
    "`run_in_background: true`.",
    expect_names=1,
    expect_bg=1,
)
check(
    "the suppression marker silences a paragraph that documents the rule",
    'Never pass `name:` to a helper without `isolation:` — `subagent_type` alone is '
    "safe. <!-- spawn-lint: ok -->",
)

# --- paragraph splitting and line numbers ------------------------------------
two_blocks = (
    "First paragraph, nothing to see.\n"
    "\n"
    'Spawn with `Agent`, `agentType: "x"`, `name: "y"`.\n'
)
found = validate.spawn_lint(two_blocks)
if len(found) != 1:
    failures.append(f"line numbers: expected 1 finding, got {found}")
elif found[0][0] != 3:
    failures.append(f"line numbers: expected the finding on line 3, got line {found[0][0]}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("spawn lint: all cases pass")
