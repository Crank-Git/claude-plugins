#!/usr/bin/env python3
"""Run a test command and print its output without the passing-test lines.

    python3 test-filter.py -- '<test command>'

The `trim-test-output.py` hook wraps a recognized test command in this script, so the
agent reads the failures and the summary, not one line per passing test. The script:

* runs the command in the shell from `$SHELL` (else `/bin/sh`), with stderr merged into
  stdout;
* drops only the lines that `PASSING` identifies as a passing test, and keeps every other
  line: failure names, assertions, tracebacks, summaries and unknown lines;
* keeps the last `TAIL_LINES` lines unchanged on a non-zero exit;
* leaves the output unchanged when fewer than `MIN_DROPPED` lines would go;
* exits with the exit code of the command, or 128 plus the signal number.

`ISSUE_FLOW_TRIM=off` in the environment prints the output unchanged. If the trim itself
fails, the script prints the output unchanged.
"""

import os
import re
import subprocess
import sys

TAIL_LINES = 20
MIN_DROPPED = 10

# Each pattern matches one line of one runner that reports a passing or skipped test.
PASSING = [re.compile(pattern) for pattern in (
    r"^\S.* \.\.\. (ok|skipped\b.*|expected failure)$",  # unittest -v
    r"^\S+ (PASSED|SKIPPED|XFAIL)\b.*$",                 # pytest -v
    r"^\S+\.py [.s]+\s*\[\s*\d+%\]$",                    # pytest progress, no failure
    r"^[.s]+\s*(\[\s*\d+%\])?$",                         # unittest or pytest dots
    r"^=== (RUN|PAUSE|CONT|NAME)\s",                     # go test -v
    r"^\s*--- (PASS|SKIP): ",                            # go test -v
    r"^PASS\b",                                          # go and jest
    r"^test \S+ \.\.\. (ok|ignored)$",                   # cargo test
    r"^\s*[✓✔√]\s",                       # jest, vitest, mocha
)]


def passing(line):
    """Return True when the line reports one passing or skipped test."""
    return any(pattern.match(line) for pattern in PASSING)


def trim(output, exit_code):
    """Return the output without its passing-test lines, with a note when lines go."""
    lines = output.split("\n")
    protected = len(lines) - TAIL_LINES if exit_code != 0 else len(lines)
    kept = [line for index, line in enumerate(lines)
            if index >= protected or not passing(line)]
    dropped = len(lines) - len(kept)
    if dropped < MIN_DROPPED:
        return output
    note = (f"[issue-flow: dropped {dropped} passing-test lines. "
            "Put ISSUE_FLOW_TRIM=off before the command to see every line.]")
    return note + "\n" + "\n".join(kept)


def main(argv):
    if len(argv) != 3 or argv[1] != "--":
        print("usage: test-filter.py -- '<test command>'", file=sys.stderr)
        return 2
    shell = os.environ.get("SHELL") or "/bin/sh"
    result = subprocess.run([shell, "-c", argv[2]], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    output = result.stdout.decode("utf-8", errors="replace")
    code = result.returncode if result.returncode >= 0 else 128 - result.returncode
    if os.environ.get("ISSUE_FLOW_TRIM", "").strip().lower() != "off":
        try:
            output = trim(output, code)
        except Exception:  # an output the trim cannot read goes out unchanged
            pass
    sys.stdout.write(output)
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
