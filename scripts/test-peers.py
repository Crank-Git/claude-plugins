#!/usr/bin/env python3
"""Tests for issue-flow/scripts/peers.py.

    python3 scripts/test-peers.py

The fixtures below are the real shapes read off disk during issue #25: an
in-process peer under the default `auto` backend, a `tmux`-backed peer from a
pinned session, undrained mail, and the lead entry that must never be reported as
a peer. The detector runs against a temporary directory, never the real
~/.claude/teams.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "peers", os.path.join(ROOT, "issue-flow", "scripts", "peers.py")
)
peers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peers)

failures = []
NOW = 1786681000.0


def fail(message):
    failures.append(message)


def make_team(root, session, members, inboxes=None):
    directory = os.path.join(root, session)
    os.makedirs(os.path.join(directory, "inboxes"), exist_ok=True)
    with open(os.path.join(directory, "config.json"), "w") as handle:
        json.dump(
            {
                "name": session,
                "leadAgentId": f"team-lead@{session}",
                "members": members,
            },
            handle,
        )
    for name, mail in (inboxes or {}).items():
        with open(os.path.join(directory, "inboxes", f"{name}.json"), "w") as handle:
            json.dump(mail, handle)
    return directory


def lead_for(session):
    """The lead entry as the harness writes it — agentId matched to the session."""
    return dict(LEAD, agentId=f"team-lead@{session}")


LEAD = {
    "agentId": "team-lead@session-aaa",
    "name": "team-lead",
    "tmuxPaneId": "leader",
    "backendType": "in-process",
    "joinedAt": int((NOW - 3600) * 1000),
}
IN_PROCESS_PEER = {
    "agentId": "probe-b@session-aaa",
    "name": "probe-b",
    "tmuxPaneId": "in-process",
    "backendType": "in-process",
    "joinedAt": int((NOW - 120) * 1000),
}
TMUX_PEER = {
    "agentId": "worker-18@session-bbb",
    "name": "worker-18",
    "tmuxPaneId": "%7",
    "backendType": "tmux",
    "joinedAt": int((NOW - 7200) * 1000),
}

work = tempfile.mkdtemp(prefix="peers-test-")
try:
    # --- the lead is not a peer -----------------------------------------------
    make_team(work, "session-lead-only", [lead_for("session-lead-only")])
    if peers.collect(work):
        fail("a team with only its lead should report no peers")

    # --- one in-process peer, with undrained mail -----------------------------
    shutil.rmtree(work)
    os.makedirs(work)
    make_team(
        work,
        "session-aaa",
        [lead_for("session-aaa"), IN_PROCESS_PEER],
        inboxes={"probe-b": [{"msg": "one"}, {"msg": "two"}]},
    )
    teams = peers.collect(work)
    if len(teams) != 1 or len(teams[0]["members"]) != 1:
        fail(f"expected exactly one peer, got {teams}")
    else:
        member = teams[0]["members"][0]
        if member["name"] != "probe-b":
            fail(f"wrong peer name: {member['name']}")
        if member["backend"] != "in-process":
            fail(f"wrong backend: {member['backend']}")
        if member["pendingMail"] != 2:
            fail(f"undrained mail miscounted: {member['pendingMail']}")

    # --- a tmux peer keeps its pane, so it can be found in the OS -------------
    make_team(work, "session-bbb", [lead_for("session-bbb"), TMUX_PEER])
    teams = peers.collect(work)
    if len(teams) != 2:
        fail(f"expected two teams with peers, got {len(teams)}")
    tmux = [m for t in teams for m in t["members"] if m["backend"] == "tmux"]
    if len(tmux) != 1 or tmux[0]["pane"] != "%7":
        fail(f"tmux peer pane not surfaced: {tmux}")

    # --- ages read the way a human reads them ---------------------------------
    if peers.age(IN_PROCESS_PEER["joinedAt"], NOW) != "2m":
        fail(f"age of a 120s peer should be 2m, got {peers.age(IN_PROCESS_PEER['joinedAt'], NOW)}")
    if peers.age(TMUX_PEER["joinedAt"], NOW) != "2h":
        fail(f"age of a 7200s peer should be 2h, got {peers.age(TMUX_PEER['joinedAt'], NOW)}")
    if peers.age(None, NOW) != "?":
        fail("a peer with no joinedAt should render '?'")

    # --- a missing or malformed team directory is not a crash -----------------
    if peers.collect(os.path.join(work, "does-not-exist")) != []:
        fail("a missing teams directory should yield no peers")
    broken = os.path.join(work, "session-broken")
    os.makedirs(broken, exist_ok=True)
    with open(os.path.join(broken, "config.json"), "w") as handle:
        handle.write("{not json")
    if len(peers.collect(work)) != 2:
        fail("an unparseable team config should be skipped, not counted or raised")

    # --- the empty case says something useful ---------------------------------
    empty = peers.render([], NOW)
    if not empty or "No live peer sessions" not in empty[0]:
        fail(f"empty render should state there are none: {empty}")
    rendered = "\n".join(peers.render(peers.collect(work), NOW))
    for expected in ("probe-b", "worker-18", "shutdown_request"):
        if expected not in rendered:
            fail(f"rendered output is missing {expected!r}")

    # --- a roster is not proof of life ----------------------------------------
    # 72 entries from a five-day-dead session were on the machine that filed
    # issue #25, and a named grandchild outlived its parent by half an hour with
    # ListAgents showing nothing. Old rosters must not read as work in flight.
    old = os.path.join(work, "session-aaa", "config.json")
    week_ago = NOW - 7 * 24 * 3600
    os.utime(old, (week_ago, week_ago))
    fresh = os.path.join(work, "session-bbb", "config.json")
    os.utime(fresh, (NOW - 60, NOW - 60))

    live, stale = peers.collect(work, stale_hours=24, now=NOW)
    if [t["session"] for t in live] != ["session-bbb"]:
        fail(f"only the freshly-touched team is live: {[t['session'] for t in live]}")
    if [t["session"] for t in stale] != ["session-aaa"]:
        fail(f"the week-old team should be stale: {[t['session'] for t in stale]}")
    if "path" not in (stale[0] if stale else {}):
        fail("a stale team must carry its path so it can be deleted")

    summary = "\n".join(peers.render(live, NOW, stale))
    if "1 stale team directory" not in summary:
        fail(f"stale teams should be summarized, not rolled out: {summary}")
    if "probe-b" in summary:
        fail("a stale team's members must not be listed as if they were live")

    # --- and --all-style inclusion still shows them ---------------------------
    everything = "\n".join(peers.render(live + stale, NOW, []))
    if "probe-b" not in everything or "worker-18" not in everything:
        fail("listing stale teams explicitly should include their members")

    # --- without stale_hours the call keeps its simple shape ------------------
    plain = peers.collect(work)
    if not isinstance(plain, list) or len(plain) != 2:
        fail(f"collect() without stale_hours should return a flat list of 2: {plain}")
finally:
    shutil.rmtree(work, ignore_errors=True)

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("peer detector: all cases pass")
