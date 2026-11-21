#!/usr/bin/env python3
"""PreToolUse hook: send the output of a test command through the plugin's test filter.

Wired by `issue-flow/hooks/hooks.json` on the `Bash` tool. When a Bash call is a
recognized test command, the hook returns `updatedInput` with the command wrapped:

    python3 <plugin>/scripts/test-filter.py -- '<the original command>'

The filter runs the original command, drops the passing-test lines, and keeps the exit
code. The agent then reads the failures and the summary, not one line per passing test.

Why `PreToolUse` and not `PostToolUse` (https://code.claude.com/docs/en/hooks, retrieved
2026-11-21): `PostToolUse` can replace a Bash result with `updatedToolOutput`, but a
command that exits non-zero fires `PostToolUseFailure`, which "can't rewrite a result".
A failing test run is the run whose output the agent must read, so only a rewrite of the
command before it runs can trim it.

The hook changes a call only when all of these are true:

* The tool is `Bash`, and the call does not run in the background.
* The permission mode is `bypassPermissions`. Claude Code checks the permission rules
  against the changed command, not the original one. In another mode, an allow rule for
  `pytest` does not match the wrapped command, and the call waits for a prompt. A
  background worker cannot answer a prompt, so the call stops.
* `ISSUE_FLOW_TRIM` is not `off` in the environment of the session, and the command does
  not start with `ISSUE_FLOW_TRIM=off`.
* Every part of the command, split at `&&` and `;`, is a recognized test command or a
  `cd` to one directory, and at least one part is a test command. A pipe, a redirection,
  a command substitution, a subshell, a background `&` or a second line makes the hook
  leave the command unchanged: the command already decides where its output goes.

The recognized test commands: `python3 -m unittest`, `python3 -m pytest`, `pytest`,
`npm test`, `npm run test`, `go test` and `cargo test`. `python` works in place of
`python3`, and `VAR=value` assignments can come first.

The wrapped command runs the `cd` inside the filter's shell, so a `cd` in a test command
does not change the working directory of later Bash calls.

The hook fails open. Any payload it does not understand, and any internal error, makes it
exit 0 with no output, and the call runs unchanged. It never returns a
`permissionDecision`.
"""

import json
import os
import re
import shlex
import sys

FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "test-filter.py")

ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SEPARATORS = {"&&", ";"}
# Characters that send output somewhere, or that run a command the hook cannot see.
UNSAFE_TEXT = ("$(", "`", "\n", "\r")
PUNCTUATION = ";&|<>()"


def is_test(words):
    """Return True when the words, after any VAR=value, are a recognized test command."""
    while words and ASSIGNMENT.match(words[0]):
        if words[0].split("=", 1)[0] == "ISSUE_FLOW_TRIM":
            return False
        words = words[1:]
    if not words:
        return False
    head = words[0]
    if head in ("python", "python3"):
        return len(words) >= 3 and words[1] == "-m" and words[2] in ("unittest", "pytest")
    if head == "pytest":
        return True
    if head == "npm":
        return words[1:2] == ["test"] or words[1:3] == ["run", "test"]
    if head in ("go", "cargo"):
        return words[1:2] == ["test"]
    return False


def recognized(command):
    """Return True when the whole command is test commands and `cd` steps only."""
    if any(text in command for text in UNSAFE_TEXT):
        return False
    if FILTER in command or "test-filter.py" in command:
        return False
    text = command.replace("2>&1", " ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    tokens = list(lexer)
    parts, current = [], []
    for token in tokens:
        if token in SEPARATORS:
            parts.append(current)
            current = []
        elif set(token) <= set(PUNCTUATION):
            return False
        else:
            current.append(token)
    parts.append(current)
    if any(not part for part in parts):
        return False
    tests = 0
    for part in parts:
        if part[0] == "cd" and len(part) == 2:
            continue
        if not is_test(part):
            return False
        tests += 1
    return tests > 0


def decide(payload):
    """Return the hook output that wraps the command, or None to leave the call alone."""
    if os.environ.get("ISSUE_FLOW_TRIM", "").strip().lower() == "off":
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    if payload.get("permission_mode") != "bypassPermissions":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("run_in_background"):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not recognized(command):
        return None
    updated = dict(tool_input)
    updated["command"] = f"python3 {shlex.quote(FILTER)} -- {shlex.quote(command)}"
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}


def main():
    try:
        output = decide(json.load(sys.stdin))
        if output is not None:
            sys.stdout.write(json.dumps(output))
    except Exception:  # the hook fails open: the call runs unchanged
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
