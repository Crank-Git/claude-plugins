#!/usr/bin/env python3
"""Read the tracker state that Stage A0 and Stage A need, in one pass.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tracker-state.py" [--since <ISO 8601>]
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tracker-state.py" --forge gitea [--since <ISO 8601>]

Run it inside a checkout of the repository: `gh` and `tea` take the owner and the
repository from the checkout's remote. `--repo owner/name` overrides that.

The output is one JSON object on stdout:

- `issues`: every open issue, with `number`, `title`, `labels`, `assignees`,
  `updatedAt`, `status` (its `status:` labels), `comments`, `commentCount` and
  `commentsTruncated`. An untriaged issue (no `status:` label) also has `body`.
- `prs`: every open pull request, with `isDraft`, `baseRefName`, `headRefName` and
  `mergeable` in place of `body`.
- `comments` on an item: with `--since`, the comments made at or after that time.
  Without `--since`, the comments of untriaged issues only.
- `commentsTruncated`: true when the read did not reach every comment that the
  rule above asks for. Then read that thread with `forge.issue.view`.
- `closedSince`: with `--since`, the issues and PRs closed or merged after it.
- `untriaged`: the numbers of the open issues with no `status:` label.
- `splitStatus`: each issue with more than one `status:` label, the label Stage A0
  step 3b keeps, and the labels it removes.
- `calls`: the number of forge calls the read made.

On GitHub the read is one `gh api graphql` query per 100 open issues. GraphQL has
no conditional (ETag) request, so the script makes one query in place of the
separate list and view calls. On Gitea it is `tea api` REST reads, 50 items a page.

Exit codes: 0 = the state is on stdout, 1 = a forge call failed (stderr names it),
2 = a usage error.
"""

import argparse
import datetime
import json
import subprocess
import sys
import urllib.parse

# Stage A0 step 3b keeps the furthest-along label on this order.
STATUS_ORDER = ["ready", "in-progress", "in-review", "awaiting-review", "needs-feedback", "blocked",
                "batched", "deploying", "deploy-failed"]
STATUS_RANK = {f"status:{name}": rank for rank, name in enumerate(STATUS_ORDER)}
GITEA_PAGE = 50
COMMENTS_PER_ITEM = 20

QUERY = """
query TrackerState($owner: String!, $name: String!, $since: DateTime, $withSince: Boolean!,
                   $withIssues: Boolean!, $withPrs: Boolean!, $issuesAfter: String, $prsAfter: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $issuesAfter, orderBy: {field: CREATED_AT, direction: ASC})
        @include(if: $withIssues) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body updatedAt
        labels(first: 30) { nodes { name } }
        assignees(first: 10) { nodes { login } }
        comments(last: 20) { totalCount nodes { databaseId author { login } createdAt body url } }
      }
    }
    pullRequests(states: OPEN, first: 50, after: $prsAfter, orderBy: {field: CREATED_AT, direction: ASC})
        @include(if: $withPrs) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title isDraft baseRefName headRefName mergeable updatedAt
        labels(first: 30) { nodes { name } }
        assignees(first: 10) { nodes { login } }
        comments(last: 20) { totalCount nodes { databaseId author { login } createdAt body url } }
      }
    }
    closedIssues: issues(states: CLOSED, first: 50, filterBy: {since: $since},
                         orderBy: {field: UPDATED_AT, direction: DESC}) @include(if: $withSince) {
      nodes { number title updatedAt }
    }
    closedPullRequests: pullRequests(states: [CLOSED, MERGED], first: 50,
                                     orderBy: {field: UPDATED_AT, direction: DESC}) @include(if: $withSince) {
      nodes { number title state updatedAt }
    }
  }
}
"""


class ForgeError(Exception):
    """A forge call failed, or its answer was an error."""


def parse_time(text):
    """Return an aware datetime for an ISO 8601 text; a bare `Z` means UTC."""
    value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def at_or_after(text, since):
    return bool(text) and parse_time(text) >= since


def run_forge(argv, binary):
    """Run one forge CLI call and return its stdout; a non-zero exit raises ForgeError."""
    result = subprocess.run([binary, *argv], capture_output=True, text=True)
    if result.returncode != 0:
        shown = " ".join(argv[:2])
        raise ForgeError(f"{binary} {shown} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def status_of(labels):
    return [label for label in labels if label.startswith("status:")]


def split_entry(item):
    known = [label for label in item["status"] if label in STATUS_RANK]
    if len(item["status"]) < 2 or not known:
        return None
    keep = max(known, key=STATUS_RANK.get)
    return {"number": item["number"], "status": item["status"], "keep": keep,
            "remove": [label for label in item["status"] if label != keep]}


def finish(item, fetched, total, since, untriaged):
    """Apply the comment rule of the module docstring to one item."""
    if since is not None:
        kept = [c for c in fetched if at_or_after(c["createdAt"], since)]
        # Only the newest comments are fetched, so an older one after --since can be missing.
        truncated = total > len(fetched) and (not fetched or at_or_after(fetched[0]["createdAt"], since))
    elif untriaged:
        kept, truncated = fetched, total > len(fetched)
    else:
        kept, truncated = [], False
    item.update(comments=kept, commentCount=total, commentsTruncated=truncated)
    return item


def summarize(issues, prs, closed, calls, since, forge):
    return {
        "forge": forge,
        "since": since.isoformat().replace("+00:00", "Z") if since else None,
        "issues": issues,
        "prs": prs,
        "closedSince": closed,
        "untriaged": [i["number"] for i in issues if not i["status"]],
        "splitStatus": [entry for entry in map(split_entry, issues) if entry],
        "calls": calls,
    }


# ---------------------------------------------------------------- GitHub


def gh_comment(node):
    return {"id": node.get("databaseId"), "author": (node.get("author") or {}).get("login", ""),
            "createdAt": node.get("createdAt", ""), "body": node.get("body", ""), "url": node.get("url", "")}


def gh_item(node, since, is_pr):
    names = [label["name"] for label in node["labels"]["nodes"]]
    item = {"number": node["number"], "title": node["title"], "labels": names,
            "assignees": [a["login"] for a in node["assignees"]["nodes"]], "updatedAt": node["updatedAt"],
            "status": status_of(names)}
    untriaged = not is_pr and not item["status"]
    if is_pr:
        item.update(isDraft=node["isDraft"], baseRefName=node["baseRefName"], headRefName=node["headRefName"],
                    mergeable=node["mergeable"])
    elif untriaged:
        item["body"] = node.get("body", "")
    block = node["comments"]
    return finish(item, [gh_comment(c) for c in block["nodes"]], block["totalCount"], since, untriaged)


def read_github(run, since=None, repo=None):
    """Return the tracker state from GitHub; `run` takes a gh argv and returns stdout."""
    since_time = parse_time(since) if since else None
    owner, name = repo.split("/", 1) if repo else ("{owner}", "{repo}")
    issues, prs, closed, calls = [], [], [], 0
    cursors = {"issues": None, "prs": None}
    want = {"issues": True, "prs": True}
    first = True
    while want["issues"] or want["prs"]:
        argv = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}",
                "-F", f"withSince={'true' if first and since else 'false'}",
                "-F", f"withIssues={'true' if want['issues'] else 'false'}",
                "-F", f"withPrs={'true' if want['prs'] else 'false'}"]
        if since:
            argv += ["-f", f"since={since}"]
        for key, variable in (("issues", "issuesAfter"), ("prs", "prsAfter")):
            if want[key] and cursors[key]:
                argv += ["-f", f"{variable}={cursors[key]}"]
        try:
            answer = json.loads(run(argv))
        except ValueError as error:
            raise ForgeError(f"gh api graphql returned text that is not JSON: {error}")
        calls += 1
        if answer.get("errors"):
            raise ForgeError("gh api graphql: " + "; ".join(e.get("message", "") for e in answer["errors"]))
        repository = (answer.get("data") or {}).get("repository")
        if repository is None:
            raise ForgeError("gh api graphql returned no repository")
        for key, field, target, is_pr in (("issues", "issues", issues, False), ("prs", "pullRequests", prs, True)):
            if not want[key]:
                continue
            connection = repository[field]
            target.extend(gh_item(node, since_time, is_pr) for node in connection["nodes"])
            want[key] = connection["pageInfo"]["hasNextPage"]
            cursors[key] = connection["pageInfo"]["endCursor"]
        if first and since:
            for node in (repository.get("closedIssues") or {}).get("nodes", []):
                closed.append({"number": node["number"], "kind": "issue", "state": "closed",
                               "title": node["title"], "updatedAt": node["updatedAt"]})
            for node in (repository.get("closedPullRequests") or {}).get("nodes", []):
                # pullRequests has no `since` filter, so the newest 50 are filtered here.
                if at_or_after(node["updatedAt"], since_time):
                    closed.append({"number": node["number"], "kind": "pr", "state": node["state"].lower(),
                                   "title": node["title"], "updatedAt": node["updatedAt"]})
        first = False
    return summarize(issues, prs, closed, calls, since_time, "github")


# ---------------------------------------------------------------- Gitea


def gitea_comment(record):
    return {"id": record["id"], "author": (record.get("user") or {}).get("login", ""),
            "createdAt": record.get("created_at", ""), "body": record.get("body", ""),
            "url": record.get("html_url", "")}


def comment_number(record):
    """The issue or PR number that a repository-wide comment belongs to."""
    url = record.get("issue_url") or record.get("pull_request_url") or record.get("html_url") or ""
    tail = url.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def names(records):
    return [entry["name"] for entry in records or []]


def logins(records):
    return [entry["login"] for entry in records or []]


def read_gitea(run, since=None, repo=None):
    """Return the tracker state from Gitea; `run` takes a tea argv and returns stdout."""
    since_time = parse_time(since) if since else None
    prefix = f"/repos/{repo}/" if repo else "/repos/{owner}/{repo}/"
    calls = 0

    def pages(path):
        nonlocal calls
        page, found = 1, []
        while True:
            joiner = "&" if "?" in path else "?"
            try:
                batch = json.loads(run(["api", f"{prefix}{path}{joiner}limit={GITEA_PAGE}&page={page}"]))
            except ValueError as error:
                raise ForgeError(f"tea api {path} returned text that is not JSON: {error}")
            calls += 1
            if not isinstance(batch, list):
                raise ForgeError(f"tea api {path} did not return a list: {str(batch)[:200]}")
            found.extend(batch)
            if len(batch) < GITEA_PAGE:
                return found
            page += 1

    issues = []
    for record in pages("issues?state=open&type=issues"):
        item = {"number": record["number"], "title": record["title"], "labels": names(record.get("labels")),
                "assignees": logins(record.get("assignees")), "updatedAt": record.get("updated_at", ""),
                "commentCount": record.get("comments", 0)}
        item["status"] = status_of(item["labels"])
        if not item["status"]:
            item["body"] = record.get("body", "")
        issues.append(item)
    prs = []
    for record in pages("pulls?state=open"):
        item = {"number": record["number"], "title": record["title"], "labels": names(record.get("labels")),
                "assignees": logins(record.get("assignees")), "updatedAt": record.get("updated_at", ""),
                "isDraft": bool(record.get("draft")), "baseRefName": (record.get("base") or {}).get("ref", ""),
                "headRefName": (record.get("head") or {}).get("ref", ""),
                "mergeable": "MERGEABLE" if record.get("mergeable") else "CONFLICTING",
                "commentCount": record.get("comments", 0)}
        item["status"] = status_of(item["labels"])
        prs.append(item)

    by_number = {item["number"]: item for item in issues + prs}
    for item in by_number.values():
        item.update(comments=[], commentsTruncated=False)
    closed = []
    if since:
        stamp = urllib.parse.quote(since, safe="")
        for record in pages(f"issues?state=closed&since={stamp}"):
            pull = record.get("pull_request")
            closed.append({"number": record["number"], "kind": "pr" if pull else "issue",
                           "state": "merged" if pull and pull.get("merged") else "closed",
                           "title": record["title"], "updatedAt": record.get("updated_at", "")})
        for record in pages(f"issues/comments?since={stamp}"):
            item = by_number.get(comment_number(record))
            if item is not None:
                item["comments"].append(gitea_comment(record))
    else:
        for item in issues:
            if not item["status"] and item["commentCount"]:
                item["comments"] = [gitea_comment(r) for r in pages(f"issues/{item['number']}/comments")]
    return summarize(issues, prs, closed, calls, since_time, "gitea")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read the Stage A0/A tracker state in one pass.")
    parser.add_argument("--forge", choices=["github", "gitea"], default="github")
    parser.add_argument("--since", help="ISO 8601 time: keep comments and closed items from then on")
    parser.add_argument("--repo", help="owner/name; the default is the repository of the checkout")
    args = parser.parse_args(argv)
    if args.since:
        try:
            parse_time(args.since)
        except ValueError:
            parser.error(f"--since is not an ISO 8601 time: {args.since!r}")
    binary = "gh" if args.forge == "github" else "tea"
    reader = read_github if args.forge == "github" else read_gitea
    try:
        state = reader(lambda call: run_forge(call, binary), since=args.since, repo=args.repo)
    except (ForgeError, OSError) as error:
        print(f"tracker-state: {error}", file=sys.stderr)
        return 1
    json.dump(state, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
