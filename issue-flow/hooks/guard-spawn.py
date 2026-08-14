#!/usr/bin/env python3
"""PreToolUse guard: refuse the one `Agent` spawn shape that strands the caller.

Wired by `issue-flow/hooks/hooks.json`, which Claude Code loads automatically for
an installed plugin. It reads the PreToolUse payload on stdin and, for an `Agent`
(or legacy `Task`) call that passes `name:` **without** `isolation:`, returns a
deny decision with the fix in the reason.

Why this is worth a hook rather than another paragraph of documentation
(measured on Claude Code 2.1.232, issue #25):

  unnamed, no isolation      -> background subagent, completion notification
  name + isolation           -> background subagent, completion notification
  name, no isolation         -> PEER SESSION: no completion notification ever,
                                invisible to ListAgents, and its plain-text
                                answer reaches nobody

The two success strings differ only in prose, so a caller cannot tell which of
the two it got. `issue-flow` never prescribes the bad shape, but the PM improvises
a `name:` at spawn sites that do not specify one, and then waits forever for a
helper that finished minutes ago.

Design choices, both deliberate:

* **Deny, not repair.** A hook may rewrite the call with `updatedInput`, but both
  repairs change behavior: adding `isolation` moves the agent into its own
  worktree cut from the default branch (it can no longer write the shared
  checkout, and `git -C` there is refused), while dropping `name` removes
  addressability the caller may have wanted. Denying returns the choice to the
  caller with both options spelled out.
* **Fail open.** Any unexpected payload, parse error, or internal fault exits 0
  and allows the call. A guard against a lost result must never become the reason
  work cannot start.

Set `ISSUE_FLOW_SPAWN_GUARD=off` to disable it for a session.
"""

import json
import os
import sys

SPAWN_TOOLS = {"Agent", "Task"}

REASON = (
    "Blocked by issue-flow's spawn guard: this Agent call passes `name` with no "
    "`isolation`, which does not create a background subagent. It creates a peer "
    "session that never fires a completion notification, does not appear in "
    "ListAgents, and whose final text is delivered to nobody — you would wait for a "
    "result that cannot arrive.\n"
    "Re-issue it one of two ways:\n"
    "  • drop `name` — an unnamed subagent always notifies (use this unless you "
    "must message the agent later); or\n"
    "  • keep `name` and add `isolation: \"worktree\"` — required for issue-flow "
    "workers, which are messaged for rework.\n"
    "Set ISSUE_FLOW_SPAWN_GUARD=off to disable this guard."
)


def decide(payload):
    """Return a deny reason, or None to allow."""
    if os.environ.get("ISSUE_FLOW_SPAWN_GUARD", "").lower() in {"off", "0", "false"}:
        return None
    if payload.get("tool_name") not in SPAWN_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    name = tool_input.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if tool_input.get("isolation"):
        return None
    return REASON


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        reason = decide(payload)
    except Exception:  # fail open — see the module docstring
        return
    if not reason:
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
