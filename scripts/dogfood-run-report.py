#!/usr/bin/env python3
"""Summarize a headless issue-flow run into evidence about where it stopped.

    python3 scripts/dogfood-run-report.py <run.jsonl> [--timeline]

Written for Sniper7Kills-LLC/claude-plugins#27, where every report so far has been
a recollection ("it only got through one or two epics"). A `claude -p
--output-format stream-json` log answers the questions those reports cannot:

* how many turns the PM took, and **where the gaps between them were** — a long
  gap with no live background work is the loop stalling, not the loop working;
* what it actually did at each epic boundary (merged? asked? stopped?);
* whether the last thing it produced was a question, which is the reported symptom;
* how many workers it spawned, so a run that stops early can be told apart from a
  run that never started anything.

The log is large and must never be read whole into a reviewing agent's context —
that is the point of this script.
"""

import argparse
import json
import re
from datetime import datetime

QUESTION = re.compile(
    r"(would you like|shall i|should i|want me to|do you want|let me know if|"
    r"confirm (?:whether|if|that)|awaiting your|ready to proceed\?)",
    re.I,
)


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load(path):
    events = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def blocks(event):
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def summarize(events):
    tools, agents, texts, stamps = [], [], [], []
    for event in events:
        stamp = parse_time(event.get("timestamp"))
        if stamp:
            stamps.append((stamp, event))
        if event.get("type") != "assistant":
            continue
        for block in blocks(event):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name")
                tools.append(name)
                if name in {"Agent", "Task"}:
                    payload = block.get("input") or {}
                    agents.append(
                        {
                            "description": payload.get("description"),
                            "subagent_type": payload.get("subagent_type"),
                            "name": payload.get("name"),
                            "isolation": payload.get("isolation"),
                        }
                    )
            elif block.get("type") == "text" and block.get("text", "").strip():
                # Only the main thread's prose; sub-agent chatter is not the PM speaking.
                if event.get("parent_tool_use_id") is None:
                    texts.append((stamp, block["text"].strip()))
    return tools, agents, texts, stamps


def gaps(stamps, threshold):
    found = []
    for (earlier, _), (later, event) in zip(stamps, stamps[1:]):
        seconds = (later - earlier).total_seconds()
        if seconds >= threshold:
            found.append((seconds, earlier, later, event.get("type")))
    return found


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("log")
    parser.add_argument("--gap", type=float, default=120.0, help="seconds counted as a stall")
    parser.add_argument("--timeline", action="store_true", help="print every PM message")
    args = parser.parse_args()

    events = load(args.log)
    tools, agents, texts, stamps = summarize(events)

    print(f"events: {len(events)}   tool calls: {len(tools)}   PM messages: {len(texts)}")
    if stamps:
        span = (stamps[-1][0] - stamps[0][0]).total_seconds()
        print(f"span: {span / 60:.1f} min   first: {stamps[0][0]:%H:%M:%S}   "
              f"last: {stamps[-1][0]:%H:%M:%S}")

    counts = {}
    for name in tools:
        counts[name] = counts.get(name, 0) + 1
    print("tools: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))

    print(f"\nworkers/agents spawned: {len(agents)}")
    for agent in agents:
        print(f"  - {agent['description']} [type={agent['subagent_type']} "
              f"name={agent['name']} isolation={agent['isolation']}]")

    stalls = gaps(stamps, args.gap)
    print(f"\ngaps over {args.gap:.0f}s: {len(stalls)}")
    for seconds, earlier, later, kind in stalls[:10]:
        print(f"  {seconds / 60:5.1f} min  {earlier:%H:%M:%S} -> {later:%H:%M:%S}  (next event: {kind})")

    if args.timeline:
        print("\n--- PM messages ---")
        for stamp, text in texts:
            when = f"{stamp:%H:%M:%S}" if stamp else "        "
            print(f"[{when}] {text[:400]}")

    if texts:
        _, last = texts[-1]
        print("\n--- final PM message ---")
        print(last[:1200])
        if QUESTION.search(last):
            print("\n>>> the run ended by ASKING THE OPERATOR — the reported symptom.")


if __name__ == "__main__":
    main()
