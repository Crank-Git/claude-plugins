#!/usr/bin/env python3
"""Seed a throwaway Gitea repo with the fixture for the loop-boundary experiment.

    python3 scripts/dogfood-loop-fixture.py --forge github --owner <you> --repo loop-dogfood
    python3 scripts/dogfood-loop-fixture.py --forge gitea --owner e --repo loop-dogfood --login telescope

Background: Sniper7Kills-LLC/claude-plugins#27. Users report that a run told to
work the whole backlog stops after one or two epics. The reports are recollections,
and they cannot separate the five candidate causes. This builds a fixture where the
only interesting variable is **whether the PM crosses an epic boundary**.

Design choices that make the measurement clean:

* **Three epics, two sub-issues each.** Enough that stopping after the first epic is
  unambiguous, small enough to run twice in an afternoon.
* **Trivial, disjoint tasks.** Each sub-issue adds one entry to one registry file
  plus one test. No sub-issue can genuinely block another, so any "blocked" verdict
  is a finding rather than the fixture's fault.
* **Acceptance criteria on every issue**, because the batch gate enforces them and a
  missing criterion would stall the run for a reason unrelated to the hypothesis.
* **No deploy target.** The standing deploy-watcher is the one background agent that
  can accidentally keep the PM alive; leaving it out means a stall is a real stall.

The script is idempotent about labels (Gitea's label create is not) and refuses to
run twice against a repo that already has fixture issues.
"""

import argparse
import json
import subprocess


LABELS = [
    ("status:ready", "#0e8a16", "Triaged and workable"),
    ("status:in-progress", "#fbca04", "A worker is building it"),
    ("status:in-review", "#fbca04", "PR open, gate pending"),
    ("status:awaiting-review", "#fbca04", "Waiting on a human approving review"),
    ("status:batched", "#c5def5", "Sub-merged into an integration branch"),
    ("status:blocked", "#b60205", "Blocked on something external"),
    ("status:needs-feedback", "#d93f0b", "Waiting on a product answer"),
    ("type:epic", "#5319e7", "Parent of sub-issues"),
    ("type:batch", "#5319e7", "Loose-issue batch tracker"),
    ("type:hotfix", "#b60205", "Urgent, bypasses batching"),
    ("priority:high", "#b60205", "Do first"),
    ("priority:low", "#bfd4f2", "Do later"),
]

# (epic title, [(sub-issue title, entry key, entry value), ...])
EPICS = [
    (
        "Epic: seed the colour entries",
        [
            ("Register the colour red", "red", "#ff0000"),
            ("Register the colour green", "green", "#00ff00"),
        ],
    ),
    (
        "Epic: seed the shape entries",
        [
            ("Register the shape circle", "circle", "round"),
            ("Register the shape square", "square", "boxy"),
        ],
    ),
    (
        "Epic: seed the size entries",
        [
            ("Register the size small", "small", "1"),
            ("Register the size large", "large", "9"),
        ],
    ),
]

SUB_BODY = """\
Add one entry to the registry.

## Task

Call `register("{key}", "{value}")` at import time in `src/registry.py`, in the
`seed_defaults()` function (create it if it does not exist, and call it from module
scope so the entry is present on import).

## Acceptance criteria

- [ ] `lookup("{key}")` returns `"{value}"` after importing `src.registry`.
- [ ] A test in `tests/` asserts exactly that, and the full suite passes with
      `python3 -m pytest -q`.

Part of #{epic}
"""

EPIC_BODY = """\
Parent issue for a slice of registry entries. Each child adds one entry and one test.

## Acceptance criteria

- [ ] Every child issue is closed.
- [ ] `python3 -m pytest -q` passes on the integration branch.

## Children

{children}
"""


FORGE = {"kind": "github", "login": None}


def api(method, path, payload=None):
    """One authenticated REST call, on whichever forge the run targets.

    Both CLIs can exit 0 while returning an error body — the Gitea token used to
    build this fixture lacked `read:issue` and `tea api` still exited 0 with a
    `{"message": "token does not have ..."}` payload. A seeding script that trusts
    the exit code silently builds half a fixture, so the error shape is checked.
    """
    if FORGE["kind"] == "github":
        command = ["gh", "api", "-X", method, path]
        if payload is not None:
            command += ["--input", "-"]
    else:
        command = ["tea", "api", "--login", FORGE["login"], "-X", method, path]
        if payload is not None:
            command += ["-d", json.dumps(payload)]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if (payload is not None and FORGE["kind"] == "github") else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} {method} {path} failed: {result.stderr.strip()}")
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    if isinstance(data, dict) and (data.get("errors") or data.get("message")):
        raise SystemExit(f"{command[0]} {method} {path} returned an error: {data}")
    return data


def ensure_labels(owner, repo):
    """Gitea's label create is not idempotent — it happily makes duplicates."""
    existing = {label["name"] for label in api("GET", f"/repos/{owner}/{repo}/labels")}
    for name, color, description in LABELS:
        if name in existing:
            continue
        # `tea` wants the leading "#", `gh` rejects it with a 422 — the same
        # difference forge.md records for label creation.
        api(
            "POST",
            f"/repos/{owner}/{repo}/labels",
            {
                "name": name,
                "color": color.lstrip("#") if FORGE["kind"] == "github" else color,
                "description": description,
            },
        )
        print(f"  label {name}")


def create_issue(owner, repo, title, body, labels, label_ids):
    # GitHub takes label names; Gitea's API takes numeric ids.
    label_field = labels if FORGE["kind"] == "github" else [label_ids[n] for n in labels]
    payload = {"title": title, "body": body, "labels": label_field}
    return api("POST", f"/repos/{owner}/{repo}/issues", payload)


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--forge", choices=["github", "gitea"], default="github")
    parser.add_argument("--login", help="tea login name (gitea only)")
    args = parser.parse_args()
    FORGE["kind"], FORGE["login"] = args.forge, args.login

    open_issues = api("GET", f"/repos/{args.owner}/{args.repo}/issues?state=all&per_page=1")
    if open_issues:
        raise SystemExit(
            f"{args.owner}/{args.repo} already has issues — seed a fresh repo so the "
            "run starts from a known state"
        )

    print("labels:")
    ensure_labels(args.owner, args.repo)
    label_ids = {
        label["name"]: label["id"]
        for label in api("GET", f"/repos/{args.owner}/{args.repo}/labels")
    }

    print("issues:")
    for epic_title, children in EPICS:
        epic = create_issue(
            args.owner, args.repo, epic_title, "placeholder",
            ["type:epic", "status:ready"], label_ids,
        )
        child_numbers = []
        for title, key, value in children:
            child = create_issue(
                args.owner, args.repo, title,
                SUB_BODY.format(key=key, value=value, epic=epic["number"]),
                ["status:ready"], label_ids,
            )
            child_numbers.append(child["number"])
            print(f"  #{child['number']} {title}")
        # The epic body carries the checklist the PM ticks, so write it last.
        api(
            "PATCH",
            f"/repos/{args.owner}/{args.repo}/issues/{epic['number']}",
            {"body": EPIC_BODY.format(
                children="\n".join(f"- [ ] #{n}" for n in child_numbers)
            )},
        )
        print(f"  #{epic['number']} {epic_title} (children: {child_numbers})")

    print("\nseeded. Fixture is 3 epics x 2 sub-issues, all status:ready, no deploy target.")


if __name__ == "__main__":
    main()
