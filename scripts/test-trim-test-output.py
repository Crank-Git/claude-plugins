#!/usr/bin/env python3
"""Tests for issue-flow/hooks/trim-test-output.py and issue-flow/scripts/test-filter.py.

    python3 scripts/test-trim-test-output.py

The hook runs as Claude Code runs it: a subprocess that reads a PreToolUse payload on
stdin. The filter runs both as a subprocess, around real commands, and as a module, on
recorded runner output. Three properties matter as much as the trim itself:

* The hook fails open. It never blocks a call and never prints a decision.
* The filter keeps the exit code of the command.
* The filter drops only lines that it can identify as a passing test.
"""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "issue-flow", "hooks", "trim-test-output.py")
FILTER = os.path.join(ROOT, "issue-flow", "scripts", "test-filter.py")

failures = []


def check(name, condition, detail=""):
    if not condition:
        failures.append(f"{name}{': ' + detail if detail else ''}")


def run_hook(payload, env=None):
    """Return (exit code, parsed stdout or None, raw stdout)."""
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_TRIM", None)
    environment.update(env or {})
    result = subprocess.run(
        [sys.executable, HOOK],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
    )
    parsed = None
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return result.returncode, parsed, result.stdout


def bash(command, mode="bypassPermissions", **extra):
    tool_input = {"command": command, "description": "Run the tests", "timeout": 120000}
    tool_input.update(extra)
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "permission_mode": mode,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test",
    }


def wrapped(command):
    return f"python3 {shlex.quote(FILTER)} -- {shlex.quote(command)}"


def expect_rewrite(command, mode="bypassPermissions"):
    code, output, raw = run_hook(bash(command, mode))
    name = f"rewrites {command!r}"
    check(name + " (exit 0)", code == 0, f"exit {code}")
    if not output:
        failures.append(f"{name}: no output, raw {raw!r}")
        return
    specific = output.get("hookSpecificOutput", {})
    check(name + " (event name)", specific.get("hookEventName") == "PreToolUse", str(output))
    check(name + " (no permission decision)", "permissionDecision" not in specific, str(output))
    updated = specific.get("updatedInput", {})
    check(name + " (wrapped command)", updated.get("command") == wrapped(command),
          repr(updated.get("command")))
    check(name + " (keeps other fields)",
          updated.get("description") == "Run the tests" and updated.get("timeout") == 120000,
          str(updated))


def expect_untouched(name, payload, env=None):
    code, output, raw = run_hook(payload, env)
    check(f"leaves {name} alone (exit 0)", code == 0, f"exit {code}")
    check(f"leaves {name} alone (no output)", raw.strip() == "", repr(raw))


# --- the hook: recognized test commands --------------------------------------
for command in [
    "python3 -m unittest",
    "python3 -m unittest -v",
    "python -m unittest discover -s tests",
    "pytest",
    "pytest -q tests/test_items.py",
    "python3 -m pytest -x",
    "npm test",
    "npm run test",
    "go test ./...",
    "cargo test",
    "python3 -m unittest -v 2>&1",
    "PYTHONPATH=. python3 -m unittest",
    "cd /work/repo && python3 -m unittest -v",
    "cd /work/repo && pytest -k 'parse and not slow'",
]:
    expect_rewrite(command)

# --- the hook: commands it must not touch ------------------------------------
for command in [
    "ls -la",
    "git status",
    "echo pytest",
    "npm install",
    "npm run build",
    "go build ./...",
    "cargo build",
    "python3 -m unittest -v | tail -20",
    "pytest > out.txt",
    "pytest < input.txt",
    "python3 -m unittest && ruff check .",
    "cd /work/repo && git status",
    "pytest $(cat args.txt)",
    "pytest `cat args.txt`",
    "pytest &",
    "python3 -m unittest\npython3 -m unittest",
    "( pytest )",
    "rtk pytest",
    "ISSUE_FLOW_TRIM=off python3 -m unittest -v",
    "pytest 'unclosed",
    wrapped("python3 -m unittest"),
    "",
]:
    expect_untouched(repr(command), bash(command))

expect_untouched("a background command", bash("pytest", run_in_background=True))
for mode in ["default", "acceptEdits", "plan", "dontAsk", "auto", None]:
    expect_untouched(f"permission mode {mode}", bash("pytest", mode=mode))
expect_untouched("the kill switch", bash("pytest"), env={"ISSUE_FLOW_TRIM": "off"})
expect_untouched("the kill switch in capitals", bash("pytest"), env={"ISSUE_FLOW_TRIM": "OFF"})
expect_untouched("another tool", {"tool_name": "Read", "permission_mode": "bypassPermissions",
                                  "tool_input": {"file_path": "/x"}})

# --- the hook fails open -----------------------------------------------------
expect_untouched("text that is not JSON", "not json {")
expect_untouched("a JSON list", "[1, 2]")
expect_untouched("a payload with no tool_input", {"tool_name": "Bash",
                                                  "permission_mode": "bypassPermissions"})
expect_untouched("a tool_input that is a string", {"tool_name": "Bash",
                                                   "permission_mode": "bypassPermissions",
                                                   "tool_input": "pytest"})
expect_untouched("a command that is a number", bash(42))
expect_untouched("an empty stdin", "")


# --- the filter as a module --------------------------------------------------
def load_filter():
    spec = importlib.util.spec_from_file_location("test_filter", FILTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    filt = load_filter()
except Exception as error:  # the tests report a missing filter, they do not crash on it
    filt = None
    failures.append(f"the filter does not load: {error!r}")

if filt is not None:
    unittest_green = "\n".join(
        [f"test_case_{i} (tests.test_items.ItemTests.test_case_{i}) ... ok" for i in range(30)]
        + ["", "-" * 70, "Ran 30 tests in 0.004s", "", "OK"]
    )
    out = filt.trim(unittest_green, 0)
    check("drops unittest ok lines", "... ok" not in out, out)
    check("keeps the unittest summary", "Ran 30 tests in 0.004s" in out and out.rstrip().endswith("OK"), out)
    check("says how many lines it dropped", "30 passing-test lines" in out, out)
    check("names the per-command opt-out", "ISSUE_FLOW_TRIM=off" in out, out)

    pytest_mixed = "\n".join(
        ["============================= test session starts ==============================",
         "collected 40 items", ""]
        + [f"tests/test_items.py::test_ok_{i} PASSED                                  [ {i:2d}%]"
           for i in range(30)]
        + ["tests/test_items.py::test_bad FAILED                                     [ 99%]",
           "", "=================================== FAILURES ===================================",
           "___________________________________ test_bad ___________________________________",
           "    def test_bad():",
           ">       assert parse('1.2') == 1.25",
           "E       AssertionError: assert 1.2 == 1.25",
           "tests/test_items.py:12: AssertionError",
           "=========================== short test summary info ============================",
           "FAILED tests/test_items.py::test_bad - AssertionError: assert 1.2 == 1.25",
           "========================= 1 failed, 30 passed in 0.05s ========================="]
    )
    out = filt.trim(pytest_mixed, 1)
    check("drops pytest PASSED lines before the last 20", "test_ok_3 PASSED" not in out, out)
    for kept in ["test_bad FAILED", "E       AssertionError: assert 1.2 == 1.25",
                 ">       assert parse('1.2') == 1.25", "tests/test_items.py:12: AssertionError",
                 "FAILED tests/test_items.py::test_bad", "1 failed, 30 passed in 0.05s"]:
        check(f"keeps the pytest line {kept!r}", kept in out, out)

    pytest_progress = "\n".join(
        [f"tests/test_{i}.py ..........                                      [{i:3d}%]" for i in range(15)]
        + ["tests/test_x.py ...F......                                       [100%]",
           "========================= 1 failed, 159 passed in 0.30s ========================="]
    )
    out = filt.trim(pytest_progress, 1)
    check("keeps a pytest progress line with an F", "tests/test_x.py ...F" in out, out)
    check("keeps the pytest summary after progress", "1 failed, 159 passed" in out, out)
    check("keeps the last lines on a non-zero exit", "tests/test_14.py" in out, out)

    pytest_progress_green = "\n".join(
        [f"tests/test_{i}.py ..........                                      [{i:3d}%]" for i in range(15)]
        + ["============================== 150 passed in 0.30s =============================="]
    )
    out = filt.trim(pytest_progress_green, 0)
    check("drops pytest dot progress lines on a pass", "tests/test_3.py" not in out, out)
    check("keeps the pytest pass summary", "150 passed in 0.30s" in out, out)

    go_output = "\n".join(
        sum([[f"=== RUN   TestOk{i}", f"--- PASS: TestOk{i} (0.00s)"] for i in range(30)], [])
        + ["=== RUN   TestBad", "    main_test.go:9: got 1, want 2", "--- FAIL: TestBad (0.00s)",
           "FAIL", "FAIL\texample.com/m\t0.004s"]
    )
    out = filt.trim(go_output, 1)
    check("drops go PASS lines", "--- PASS: TestOk3 " not in out and "=== RUN   TestOk3\n" not in out, out)
    for kept in ["main_test.go:9: got 1, want 2", "--- FAIL: TestBad", "FAIL\texample.com/m"]:
        check(f"keeps the go line {kept!r}", kept in out, out)

    cargo_output = "\n".join(
        ["running 41 tests"]
        + [f"test tests::ok_{i} ... ok" for i in range(40)]
        + ["test tests::bad ... FAILED", "", "failures:", "",
           "---- tests::bad stdout ----",
           "thread 'tests::bad' panicked at src/lib.rs:9:5:",
           "assertion `left == right` failed", "",
           "test result: FAILED. 40 passed; 1 failed; 0 ignored"]
    )
    out = filt.trim(cargo_output, 101)
    check("drops cargo ok lines", "tests::ok_4 ... ok" not in out, out)
    for kept in ["test tests::bad ... FAILED", "panicked at src/lib.rs:9:5", "test result: FAILED"]:
        check(f"keeps the cargo line {kept!r}", kept in out, out)

    jest_output = "\n".join(
        ["PASS src/a.test.js", "PASS src/b.test.js"]
        + [f"  ✓ adds item {i} (2 ms)" for i in range(40)]
        + ["FAIL src/c.test.js", "  ✕ parses a price (3 ms)",
           "    Expected: 1.25", "    Received: 1.2",
           "Tests:       1 failed, 40 passed, 41 total"]
    )
    out = filt.trim(jest_output, 1)
    check("drops jest check-mark lines", "adds item 5 " not in out, out)
    for kept in ["FAIL src/c.test.js", "parses a price", "Expected: 1.25", "Tests:       1 failed"]:
        check(f"keeps the jest line {kept!r}", kept in out, out)

    small = "test_a (t.T.test_a) ... ok\ntest_b (t.T.test_b) ... ok\n\nRan 2 tests in 0.001s\n\nOK"
    check("leaves a short output unchanged", filt.trim(small, 0) == small, filt.trim(small, 0))

    unknown = "\n".join(f"line {i} of build output" for i in range(200))
    check("leaves output with no passing-test lines unchanged", filt.trim(unknown, 0) == unknown)

    tail_ok = "\n".join(
        ["Traceback (most recent call last):", '  File "setup.py", line 3', "ImportError: no x"]
        + [f"test_late_{i} (t.T.test_late_{i}) ... ok" for i in range(40)]
    )
    out = filt.trim(tail_ok, 1)
    check("keeps the last 20 lines on a non-zero exit, even passing ones",
          all(f"test_late_{i} (t.T" in out for i in range(20, 40)), out)
    check("still drops passing lines before the last 20", "test_late_3 (t.T" not in out, out)
    check("keeps the error before the passing lines", "ImportError: no x" in out, out)


# --- the filter around real commands -----------------------------------------
def run_filter(command, cwd=None):
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_TRIM", None)
    return subprocess.run([sys.executable, FILTER, "--", command], capture_output=True,
                          text=True, cwd=cwd, env=environment)


for status in [0, 1, 3, 101]:
    result = run_filter(f"echo some output; exit {status}")
    check(f"keeps exit code {status}", result.returncode == status, f"got {result.returncode}")
    check(f"passes short output through for exit {status}", "some output" in result.stdout,
          repr(result.stdout))

result = run_filter("echo to-stderr 1>&2; exit 0")
check("merges stderr into the output", "to-stderr" in result.stdout, repr(result.stdout))

result = run_filter("kill -TERM $$")
check("reports a signal as 128 plus the signal number", result.returncode == 128 + 15,
      f"got {result.returncode}")

result = subprocess.run([sys.executable, FILTER], capture_output=True, text=True)
check("refuses a call with no command", result.returncode == 2, f"got {result.returncode}")

with tempfile.TemporaryDirectory() as tmp:
    tests = ["import unittest", "", "class T(unittest.TestCase):"]
    tests += [f"    def test_ok_{i}(self):\n        self.assertTrue(True)" for i in range(25)]
    tests += ["    def test_bad(self):\n        self.assertEqual(1, 2, 'one is not two')"]
    with open(os.path.join(tmp, "test_sample.py"), "w") as handle:
        handle.write("\n".join(tests) + "\n")
    result = run_filter(f"{shlex.quote(sys.executable)} -m unittest -v", cwd=tmp)
    out = result.stdout
    check("a real unittest run keeps exit code 1", result.returncode == 1, f"got {result.returncode}")
    check("a real unittest run drops the ok lines", "test_ok_3 (" not in out, out)
    for kept in ["test_bad (", "FAIL: test_bad", "AssertionError: 1 != 2 : one is not two",
                 "Ran 26 tests", "FAILED (failures=1)"]:
        check(f"a real unittest run keeps {kept!r}", kept in out, out)

    result = subprocess.run([sys.executable, FILTER, "--", f"{shlex.quote(sys.executable)} -m unittest -v"],
                            capture_output=True, text=True, cwd=tmp,
                            env={**os.environ, "ISSUE_FLOW_TRIM": "off"})
    check("the kill switch makes the filter pass everything through",
          "test_ok_3 (" in result.stdout and result.returncode == 1, result.stdout)

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("trim hook and test filter: all cases pass")
