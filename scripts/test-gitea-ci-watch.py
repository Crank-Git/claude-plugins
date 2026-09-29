#!/usr/bin/env python3
"""Tests for issue-flow/scripts/gitea-ci-watch.sh.

    python3 scripts/test-gitea-ci-watch.py

Runs the real script under bash, with a stub `tea` that replays a queue of
responses (one per call) and logs the arguments it was given. Every verdict path
is covered, and so is every way the request can fail: a failed request must
never read as green, and an API error must not read as "no run".
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "scripts", "gitea-ci-watch.sh")
SHA = "0123456789abcdef0123456789abcdef01234567"

STUB = """#!/usr/bin/env bash
# Replays the next queued response. A line "EXIT <n>" fails the call with code n.
printf '%s\\n' "$*" >> "$STUB_DIR/args"
n=$(cat "$STUB_DIR/count" 2>/dev/null || echo 0)
n=$((n + 1)); echo "$n" > "$STUB_DIR/count"
line=$(sed -n "${n}p" "$STUB_DIR/queue")
case "$line" in
  EXIT*) exit "${line#EXIT }" ;;
  "") line=$(tail -n 1 "$STUB_DIR/queue") ;;   # past the end: repeat the last one
esac
printf '%s\\n' "$line"
"""

failures = []


def runs(*items):
    return json.dumps({"total_count": len(items), "workflow_runs": list(items)})


def run_(status, conclusion=None):
    return {"status": status, "conclusion": conclusion, "head_sha": SHA}


def watch(queue, sha=SHA, **env):
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "tea")
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, 0o755)
        with open(os.path.join(d, "queue"), "w") as f:
            f.write("\n".join(queue) + "\n")
        environment = dict(os.environ)
        environment.update(
            {
                "IFW_TEA": stub,
                "STUB_DIR": d,
                "IFW_NONE_SLEEP": "0",
                "IFW_RUN_SLEEP": "0",
                **{k: str(v) for k, v in env.items()},
            }
        )
        result = subprocess.run(
            ["bash", SCRIPT, sha], capture_output=True, text=True, env=environment, timeout=60
        )
        args_path = os.path.join(d, "args")
        args = open(args_path).read().splitlines() if os.path.exists(args_path) else []
        return result.returncode, result.stdout.strip(), args


def expect(name, queue, verdict, code=0, **env):
    got_code, got, args = watch(queue, **env)
    if (got, got_code) != (verdict, code):
        failures.append(f"{name}: expected {verdict!r} exit {code}, got {got!r} exit {got_code}")
    return args


# --- terminal verdicts ----------------------------------------------------------
args = expect("an immediate green run is success", [runs(run_("completed", "success"))], "success")
if not args or f"head_sha={SHA}" not in args[0] or "/actions/runs" not in args[0]:
    failures.append(f"the request must be anchored to the commit: {args}")
expect(
    "running runs are waited out",
    [runs(run_("in_progress")), runs(run_("queued")), runs(run_("completed", "success"))],
    "success",
)
expect(
    "one red workflow fails the commit",
    [runs(run_("completed", "success"), run_("completed", "failure"))],
    "failure",
)
expect(
    "a workflow skipped by its own if: is a pass",
    [runs(run_("completed", "success"), run_("completed", "skipped"))],
    "success",
)
expect("a completed run with no conclusion is not a pass", [runs(run_("completed"))], "failure")
expect(
    "timed_out is a failure, not an unknown that falls through",
    [runs(run_("completed", "timed_out"))],
    "failure",
)
expect(
    "one run still going keeps the watch open",
    [runs(run_("completed", "success"), run_("in_progress")), runs(run_("completed", "success"), run_("completed", "success"))],
    "success",
)
expect(
    "the .runs shape is read too",
    [json.dumps({"runs": [run_("completed", "success")]})],
    "success",
)

# --- no run, and running out of time ---------------------------------------------
expect(
    "no run for the whole window is no-run-registered",
    [runs()],
    "no-run-registered",
    IFW_NONE_LIMIT=3,
)
expect(
    "a run that registers late is watched, not written off",
    [runs(), runs(), runs(run_("completed", "success"))],
    "success",
    IFW_NONE_LIMIT=3,
)
expect(
    "runs still going at the limit is timed-out",
    [runs(run_("in_progress"))],
    "timed-out",
    IFW_MAX=3,
)

# --- failed requests never read as green, or as "no run" -------------------------
expect(
    "an API error object is watch-error, not no-run-registered",
    ['{"errors": null, "message": "not found", "url": "http://x/api/swagger"}'],
    "watch-error",
    code=1,
)
expect("a failed tea call is watch-error", ["EXIT 1"], "watch-error", code=1)
expect("an empty response is watch-error", [""], "watch-error", code=1)
expect("a non-JSON response is watch-error", ["<html>502 Bad Gateway</html>"], "watch-error", code=1)
expect("a JSON list is not a run list", ["[]"], "watch-error", code=1)

# --- arguments -------------------------------------------------------------------
code, out, args = watch([runs(run_("completed", "success"))], sha="abc123")
if code != 2 or args:
    failures.append(f"an abbreviated sha must be refused before any request: exit {code}, calls {args}")
code, out, args = watch([runs(run_("completed", "success"))], sha="")
if code != 2:
    failures.append(f"a missing sha must be refused: exit {code}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("gitea ci watch: all cases pass")
