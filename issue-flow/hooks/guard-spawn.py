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

`ISSUE_FLOW_SPAWN_GUARD` selects the behavior: `deny` (default), `ask` (prompt
instead of refusing), or `off`.

**`ask` is for interactive sessions only.** Measured: a background agent whose
spawn hits an `ask` decision **hangs** — there is nobody to answer the prompt, so
the tool call never returns and the agent sits at its last line until it is
killed. Every issue-flow worker is a background agent, so `ask` would trade a
recoverable refusal for exactly the silent stall this guard exists to prevent.
That is why the default is `deny`, which returns an actionable error the agent can
act on by itself. A named peer session is a legitimate thing to
want — the harness offers it deliberately — and a plugin has no business refusing
it in a project that is not running issue-flow. Hooks read the environment as it
was when the session started, so `export` from a tool call does nothing: set it in
the project's `.claude/settings.json` instead and start a new session.

    { "env": { "ISSUE_FLOW_SPAWN_GUARD": "off" } }

For reference, `issue-flow` itself never trips this guard: its only named spawn is
`worker-<issue>`, which always carries `isolation: "worktree"`. What trips it is an
improvised `name:` at a spawn site that did not specify one — the failure in
issue #25.
"""

import json
import os
import sys

SPAWN_TOOLS = {"Agent", "Task"}

REASON = (
    "issue-flow spawn guard: this Agent call passes `name` with no `isolation`, which "
    "does not create a background subagent. It creates a peer session — no completion "
    "notification ever, invisible to ListAgents, and its final text delivered to "
    "nobody. You would wait for a result that cannot arrive.\n"
    "\n"
    "**Drop `name`.** An unnamed subagent always notifies, and it still runs in your "
    "worktree on your branch, so its work reaches you. This is the fix in almost every "
    "case — including a worker spawning its own review or fix children.\n"
    "\n"
    "Add `isolation: \"worktree\"` **only** if you also need the agent addressable by "
    "`SendMessage` later, as the PM does for `worker-<issue>`. Do not add it to a "
    "worker's own child: an isolated child builds in a separate worktree and its "
    "commits never reach your branch (agents/issue-worker.md).\n"
    "\n"
    "Deliberately want a peer session? This guard reads its setting from the "
    "environment at session start, so exporting a variable now will not change it — "
    "put this in the project's .claude/settings.json and start a new session:\n"
    '    "env": { "ISSUE_FLOW_SPAWN_GUARD": "off" }'
)

ASK_REASON = REASON.replace(
    "issue-flow spawn guard:", "issue-flow spawn guard (approve only if deliberate):", 1
)


def mode():
    """off | ask | deny — read once per call, from the session's environment."""
    setting = os.environ.get("ISSUE_FLOW_SPAWN_GUARD", "").strip().lower()
    if setting in {"off", "0", "false", "no"}:
        return "off"
    if setting == "ask":
        return "ask"
    return "deny"


def decide(payload):
    """Return (permissionDecision, reason), or None to stay out of the way."""
    guard = mode()
    if guard == "off":
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
    return ("ask", ASK_REASON) if guard == "ask" else ("deny", REASON)


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        decision = decide(payload)
    except Exception:  # fail open — see the module docstring
        return
    if not decision:
        return
    permission, reason = decision
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": permission,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
