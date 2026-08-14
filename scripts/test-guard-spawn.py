#!/usr/bin/env python3
"""Tests for issue-flow/hooks/guard-spawn.py.

    python3 scripts/test-guard-spawn.py

Runs the hook as Claude Code runs it — a subprocess fed a PreToolUse payload on
stdin — and checks the decision. The payload shapes are the ones measured in
issue #25. Two properties matter as much as the blocking itself: the guard must
never block a spawn that would have worked, and it must fail open on anything it
does not understand.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "issue-flow", "hooks", "guard-spawn.py")

failures = []


def run(payload, env=None):
    """Return the parsed hook output, or None when it allowed the call."""
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_SPAWN_GUARD", None)
    environment.update(env or {})
    result = subprocess.run(
        [sys.executable, HOOK],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        failures.append(f"hook exited {result.returncode}: {result.stderr.strip()}")
        return None
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        failures.append(f"hook emitted non-JSON: {result.stdout[:120]!r}")
        return None


def denied(output):
    return bool(output) and output.get("hookSpecificOutput", {}).get(
        "permissionDecision"
    ) == "deny"


def expect(name, payload, should_deny, env=None):
    output = run(payload, env)
    if denied(output) != should_deny:
        verb = "deny" if should_deny else "allow"
        failures.append(f"{name}: expected the guard to {verb}, got {output}")
    return output


def spawn(**tool_input):
    return {"tool_name": "Agent", "tool_input": tool_input}


# --- the shape that strands the caller ---------------------------------------
output = expect(
    "named spawn without isolation is denied",
    spawn(name="reviewer", subagent_type="general-purpose", prompt="review the diff"),
    True,
)
if output:
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    for expected in ("drop `name`", 'isolation: "worktree"', "ISSUE_FLOW_SPAWN_GUARD=off"):
        if expected not in reason:
            failures.append(f"the deny reason should offer {expected!r}: {reason[:200]}")
    if output["hookSpecificOutput"].get("hookEventName") != "PreToolUse":
        failures.append("the decision must name its hook event")

# --- shapes that must pass untouched -----------------------------------------
expect(
    "named spawn with isolation is allowed",
    spawn(name="worker-42", isolation="worktree", subagent_type="issue-flow:issue-worker"),
    False,
)
expect("unnamed spawn is allowed", spawn(subagent_type="Explore", prompt="locate"), False)
expect("unnamed isolated spawn is allowed", spawn(isolation="worktree", prompt="x"), False)
expect("an empty name is not a name", spawn(name="   ", prompt="x"), False)
expect(
    "a Bash call is none of the guard's business",
    {"tool_name": "Bash", "tool_input": {"command": "gh pr checks 21", "run_in_background": True}},
    False,
)
expect(
    "run_in_background alone is not blocked",
    spawn(subagent_type="general-purpose", run_in_background=True, prompt="x"),
    False,
)

# --- the legacy tool name is covered too --------------------------------------
expect(
    "a legacy Task spawn is guarded the same way",
    {"tool_name": "Task", "tool_input": {"name": "reviewer", "prompt": "x"}},
    True,
)

# --- the escape hatch ---------------------------------------------------------
expect(
    "ISSUE_FLOW_SPAWN_GUARD=off disables the guard",
    spawn(name="reviewer", prompt="x"),
    False,
    env={"ISSUE_FLOW_SPAWN_GUARD": "off"},
)

# --- fail open: a guard must never be the reason work cannot start ------------
expect("malformed JSON allows the call", "{not json", False)
expect("an empty payload allows the call", {}, False)
expect("a null tool_input allows the call", {"tool_name": "Agent", "tool_input": None}, False)
expect("a string tool_input allows the call", {"tool_name": "Agent", "tool_input": "x"}, False)
expect("empty stdin allows the call", "", False)

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("spawn guard: all cases pass")
