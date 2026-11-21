#!/usr/bin/env python3
"""Tests for issue-flow/scripts/tracker-state.py and the fake `gh` call it makes.

    python3 scripts/test-tracker-state.py

The unit tests give the script a stub forge, so they make no network call. The end
to end tests run the script against evals/fakegh/gh, the fake that the PM evals use.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "scripts", "tracker-state.py")
FAKE_DIR = os.path.join(ROOT, "evals", "fakegh")
spec = importlib.util.spec_from_file_location("tracker_state", SCRIPT)
tracker_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker_state)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


class Stub:
    """A forge that answers each call with the next canned reply and keeps the argv."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        reply = self.replies.pop(0) if self.replies else None
        if callable(reply):
            reply = reply(argv)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)


def labels(*names):
    return {"nodes": [{"name": name} for name in names]}


def comments(total, *nodes):
    return {"totalCount": total, "nodes": [
        {"databaseId": cid, "author": {"login": who}, "createdAt": at, "body": body,
         "url": f"https://github.com/acme/widgets/issues/1#issuecomment-{cid}"}
        for cid, who, at, body in nodes]}


def gh_issue(number, names=(), body="", updated="2026-11-20T09:00:00Z", comment_block=None, assignees=()):
    return {"number": number, "title": f"Issue {number}", "body": body, "updatedAt": updated,
            "labels": labels(*names), "assignees": {"nodes": [{"login": a} for a in assignees]},
            "comments": comment_block or comments(0)}


def gh_page(issues=None, prs=None, issues_next=None, prs_next=None, closed_issues=None, closed_prs=None):
    repository = {}
    if issues is not None:
        repository["issues"] = {"pageInfo": {"hasNextPage": issues_next is not None, "endCursor": issues_next},
                                "nodes": issues}
    if prs is not None:
        repository["pullRequests"] = {"pageInfo": {"hasNextPage": prs_next is not None, "endCursor": prs_next},
                                      "nodes": prs}
    if closed_issues is not None:
        repository["closedIssues"] = {"nodes": closed_issues}
    if closed_prs is not None:
        repository["closedPullRequests"] = {"nodes": closed_prs}
    return {"data": {"repository": repository}}


def field(argv, key):
    """The value of `-F key=value` or `-f key=value` in a gh argv, or None."""
    for index, arg in enumerate(argv[:-1]):
        if arg in ("-F", "-f") and argv[index + 1].startswith(key + "="):
            return argv[index + 1].split("=", 1)[1]
    return None


def test_github_pages_until_both_lists_end():
    pr = {"number": 9, "title": "PR 9", "isDraft": True, "baseRefName": "batch/8-x", "headRefName": "issue/7-y",
          "mergeable": "MERGEABLE", "updatedAt": "2026-11-20T09:00:00Z", "labels": labels(),
          "assignees": {"nodes": []}, "comments": comments(0)}
    stub = Stub([
        gh_page(issues=[gh_issue(1, ["status:ready"])], prs=[pr], issues_next="CUR1"),
        gh_page(issues=[gh_issue(2, ["status:ready"])]),
    ])
    state = tracker_state.read_github(stub)
    check([i["number"] for i in state["issues"]] == [1, 2], f"both pages of issues are kept, got {state['issues']}")
    check([p["number"] for p in state["prs"]] == [9], f"the PR list is read once, got {state['prs']}")
    check(state["calls"] == 2, f"two pages cost two calls, got {state['calls']}")
    second = stub.calls[1]
    check(field(second, "issuesAfter") == "CUR1", f"page 2 passes the issue cursor, got {second}")
    check(field(second, "withPrs") == "false", f"page 2 does not read the finished PR list again, got {second}")
    check(stub.calls[0][:2] == ["api", "graphql"], f"the read is one gh api graphql call, got {stub.calls[0][:2]}")
    query = field(stub.calls[0], "query") or ""
    check("query TrackerState" in query, "the query is named TrackerState, so the fake can recognize it")
    check(field(stub.calls[0], "owner") == "{owner}" and field(stub.calls[0], "name") == "{repo}",
          f"without --repo, gh fills the owner and the name from the checkout, got {stub.calls[0]}")


def test_github_maps_one_pass_for_stage_a():
    since = "2026-11-21T00:00:00Z"
    stub = Stub([gh_page(
        issues=[
            gh_issue(1, body="untriaged body", comment_block=comments(1, (501, "maintainer", "2026-11-19T00:00:00Z", "old"))),
            gh_issue(2, ["status:ready"], body="triaged body", assignees=["eval-bot"],
                     comment_block=comments(1, (502, "maintainer", "2026-11-21T10:00:00Z", "new"))),
            gh_issue(3, ["status:in-review", "status:batched", "type:epic"],
                     comment_block=comments(40, (503, "a", "2026-11-21T11:00:00Z", "x"))),
            gh_issue(4, ["status:ready", "status:blocked"]),
        ],
        prs=[],
        closed_issues=[{"number": 5, "title": "Closed", "updatedAt": "2026-11-21T12:00:00Z"}],
        closed_prs=[{"number": 6, "title": "Merged", "state": "MERGED", "updatedAt": "2026-11-21T13:00:00Z"},
                    {"number": 7, "title": "Old", "state": "CLOSED", "updatedAt": "2026-11-01T00:00:00Z"}],
    )])
    state = tracker_state.read_github(stub, since=since)
    issues = {i["number"]: i for i in state["issues"]}
    check(field(stub.calls[0], "since") == since and field(stub.calls[0], "withSince") == "true",
          f"--since reaches the query, got {stub.calls[0]}")
    check(issues[1].get("body") == "untriaged body", f"an untriaged issue carries its body, got {issues[1]}")
    check("body" not in issues[2], f"a triaged issue carries no body, got {issues[2]}")
    check(issues[2]["labels"] == ["status:ready"] and issues[2]["assignees"] == ["eval-bot"],
          f"labels and assignees are plain names, got {issues[2]}")
    check(issues[3]["status"] == ["status:in-review", "status:batched"], f"status keeps only status: labels, got {issues[3]}")
    check([c["id"] for c in issues[2]["comments"]] == [502], f"--since keeps the new comment, got {issues[2]}")
    check(issues[1]["comments"] == [], f"--since drops a comment older than the timestamp, got {issues[1]}")
    check(issues[3]["commentsTruncated"] is True,
          f"40 comments with all 1 fetched after --since is a truncated thread, got {issues[3]}")
    check(issues[2]["commentsTruncated"] is False, f"a complete thread is not truncated, got {issues[2]}")
    check(state["untriaged"] == [1], f"untriaged lists the issues with no status label, got {state['untriaged']}")
    split = {s["number"]: s for s in state["splitStatus"]}
    check(sorted(split) == [3, 4], f"splitStatus finds the issues with two status labels, got {state['splitStatus']}")
    check(split.get(3, {}).get("keep") == "status:batched" and split.get(3, {}).get("remove") == ["status:in-review"],
          f"a fact outranks a working label, got {split.get(3)}")
    check(split.get(4, {}).get("keep") == "status:blocked", f"a park outranks status:ready, got {split.get(4)}")
    moved = [(m["number"], m["kind"], m["state"]) for m in state["closedSince"]]
    check(moved == [(5, "issue", "closed"), (6, "pr", "merged")],
          f"closedSince keeps the items closed after --since, got {moved}")


def test_github_without_since_reads_untriaged_threads_only():
    stub = Stub([gh_page(issues=[
        gh_issue(1, comment_block=comments(1, (501, "maintainer", "2026-11-19T00:00:00Z", "context"))),
        gh_issue(2, ["status:ready"], comment_block=comments(1, (502, "maintainer", "2026-11-19T00:00:00Z", "plan"))),
    ], prs=[])])
    state = tracker_state.read_github(stub)
    issues = {i["number"]: i for i in state["issues"]}
    check([c["body"] for c in issues[1]["comments"]] == ["context"], f"an untriaged thread is kept, got {issues[1]}")
    check(issues[2]["comments"] == [], f"without --since a triaged thread is left out, got {issues[2]}")
    check(field(stub.calls[0], "withSince") == "false", f"without --since the closed lists are skipped, got {stub.calls[0]}")
    check(state["closedSince"] == [], "without --since closedSince is empty")


def test_github_repo_flag_and_errors():
    stub = Stub([gh_page(issues=[], prs=[])])
    tracker_state.read_github(stub, repo="octo/demo")
    check(field(stub.calls[0], "owner") == "octo" and field(stub.calls[0], "name") == "demo",
          f"--repo sets the owner and the name, got {stub.calls[0]}")
    stub = Stub([{"errors": [{"message": "Field 'x' doesn't exist"}]}])
    try:
        tracker_state.read_github(stub)
        check(False, "a GraphQL error response raises ForgeError")
    except tracker_state.ForgeError as error:
        check("doesn't exist" in str(error), f"the ForgeError names the GraphQL error, got {error}")


def gitea_issue(number, names=(), body="", comments_count=0, pull=None, state="open",
                updated="2026-11-20T09:00:00+00:00"):
    return {"number": number, "title": f"Issue {number}", "body": body, "state": state, "updated_at": updated,
            "labels": [{"name": n} for n in names], "assignees": [{"login": "eval-bot"}] if names else None,
            "comments": comments_count, "pull_request": pull}


def gitea_comment(cid, number, at, body, kind="issues"):
    return {"id": cid, "body": body, "created_at": at, "user": {"login": "maintainer"},
            "html_url": f"https://gitea.example/acme/widgets/{kind}/{number}#issuecomment-{cid}",
            "issue_url": f"https://gitea.example/api/v1/repos/acme/widgets/issues/{number}"}


def test_gitea_pages_and_maps():
    full_page = [gitea_issue(n, ["status:ready"]) for n in range(1, 51)]
    replies = {
        "issues?state=open&type=issues&limit=50&page=1": full_page,
        "issues?state=open&type=issues&limit=50&page=2": [gitea_issue(51), gitea_issue(52, ["status:in-review", "status:batched"])],
        "pulls?state=open&limit=50&page=1": [{
            "number": 60, "title": "PR", "draft": True, "mergeable": False, "updated_at": "2026-11-20T09:00:00+00:00",
            "base": {"ref": "batch/8-x"}, "head": {"ref": "issue/7-y"}, "labels": [], "assignees": None, "comments": 0}],
        "issues?state=closed&since=2026-11-21T00%3A00%3A00Z&limit=50&page=1": [
            gitea_issue(70, state="closed", updated="2026-11-21T01:00:00+00:00"),
            gitea_issue(71, state="closed", pull={"merged": True}, updated="2026-11-21T02:00:00+00:00")],
        "issues/comments?since=2026-11-21T00%3A00%3A00Z&limit=50&page=1": [
            gitea_comment(900, 51, "2026-11-21T03:00:00+00:00", "answer"),
            gitea_comment(901, 60, "2026-11-21T04:00:00+00:00", "review", kind="pulls")],
    }

    def answer(argv):
        path = argv[1].split("/repos/{owner}/{repo}/", 1)[1]
        if path not in replies:
            return RuntimeError(f"unexpected call {argv}")
        return replies[path]

    stub = Stub([answer] * 10)
    state = tracker_state.read_gitea(stub, since="2026-11-21T00:00:00Z")
    issues = {i["number"]: i for i in state["issues"]}
    check(len(issues) == 52, f"a full page of 50 asks for the next page, got {len(issues)} issues")
    check(all(call[0] == "api" for call in stub.calls), f"every Gitea read is a tea api call, got {stub.calls}")
    check(state["calls"] == 5, f"two issue pages, one PR page, one closed page and one comment page, got {state['calls']}")
    check(issues[51].get("body") == "" and state["untriaged"] == [51], f"untriaged is #51, got {state['untriaged']}")
    check([c["id"] for c in issues[51]["comments"]] == [900], f"a repo-wide comment lands on its issue, got {issues[51]}")
    pr = state["prs"][0] if state["prs"] else {}
    check(pr.get("isDraft") is True and pr.get("mergeable") == "CONFLICTING" and pr.get("baseRefName") == "batch/8-x",
          f"a Gitea PR maps to the same fields as a GitHub PR, got {pr}")
    check([c["id"] for c in pr.get("comments", [])] == [901], f"a PR comment lands on the PR, got {pr}")
    check([s["number"] for s in state["splitStatus"]] == [52], f"split status works on Gitea, got {state['splitStatus']}")
    moved = [(m["number"], m["kind"], m["state"]) for m in state["closedSince"]]
    check(moved == [(70, "issue", "closed"), (71, "pr", "merged")], f"closedSince maps Gitea items, got {moved}")


def test_gitea_without_since_reads_each_untriaged_thread():
    replies = {
        "issues?state=open&type=issues&limit=50&page=1": [gitea_issue(1, comments_count=1), gitea_issue(2, ["status:ready"], comments_count=3)],
        "pulls?state=open&limit=50&page=1": [],
        "issues/1/comments?limit=50&page=1": [gitea_comment(800, 1, "2026-11-19T00:00:00+00:00", "context")],
    }
    stub = Stub([lambda argv: replies.get(argv[1].split("/repos/{owner}/{repo}/", 1)[1], RuntimeError(argv))] * 5)
    state = tracker_state.read_gitea(stub)
    issues = {i["number"]: i for i in state["issues"]}
    check([c["body"] for c in issues[1]["comments"]] == ["context"], f"the untriaged thread is read, got {issues[1]}")
    check(state["calls"] == 3, f"the triaged thread is not read, got {state['calls']} calls: {stub.calls}")


# ---------------------------------------------------------------- end to end, through the fake gh


def run_script(work, *args):
    env = dict(os.environ, PATH=FAKE_DIR + os.pathsep + os.environ.get("PATH", ""),
               IFE_TRACKER=os.path.join(work, "tracker.json"), IFE_GH_LOG=os.path.join(work, "gh-log.jsonl"))
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env, cwd=work)


def with_case(case):
    work = tempfile.mkdtemp(prefix="tracker-state-test-")
    shutil.copy(os.path.join(ROOT, "evals", "pm", case, "tracker.json"), os.path.join(work, "tracker.json"))
    return work


def read_log(work):
    path = os.path.join(work, "gh-log.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_end_to_end_split_status_case():
    work = with_case("pm-a0-split-status")
    try:
        result = run_script(work)
        check(result.returncode == 0, f"the script exits 0 on the fake, got {result.returncode}: {result.stderr}")
        state = json.loads(result.stdout or "{}")
        keep = {s["number"]: s["keep"] for s in state.get("splitStatus", [])}
        check(keep == {21: "status:needs-feedback", 22: "status:batched", 24: "status:blocked"},
              f"the a0 case has three split issues with the Stage A0 keep labels, got {keep}")
        log = read_log(work)
        check([(e["cmd"], e["kind"]) for e in log] == [("api", "read")],
              f"one pass is one logged read, got {[(e['cmd'], e['kind']) for e in log]}")
        check(log and log[0]["argv"][:2] == ["api", "graphql"], f"the logged call is gh api graphql, got {log[:1]}")
        check(log and set(log[0]) == {"seq", "argv", "cmd", "time", "kind", "exit"},
              f"the log line keeps its format, got {log[:1]}")

        result = run_script(work, "--since", "2026-11-21T00:00:00Z")
        state = json.loads(result.stdout or "{}")
        check(all(i["comments"] == [] for i in state.get("issues", [])),
              f"no comment in the case is newer than --since, got {state.get('issues')}")
        result = run_script(work, "--since", "2026-11-19T00:00:00Z")
        state = json.loads(result.stdout or "{}")
        with_comments = sorted(i["number"] for i in state.get("issues", []) if i["comments"])
        check(with_comments == [21, 24], f"the 2026-11-20 comments are newer than 2026-11-19, got {with_comments}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_end_to_end_triage_case():
    work = with_case("pm-triage")
    try:
        result = run_script(work)
        state = json.loads(result.stdout or "{}")
        check(state.get("untriaged") == [1, 2], f"the triage case has #1 and #2 untriaged, got {state.get('untriaged')}")
        issues = {i["number"]: i for i in state.get("issues", [])}
        check("--json flag" in issues.get(1, {}).get("body", "") or "machine-readable" in issues.get(1, {}).get("body", ""),
              f"#1 carries its body, got {issues.get(1)}")
        check("body" not in issues.get(4, {"body": None}), f"#4 is triaged and carries no body, got {issues.get(4)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_end_to_end_cross_check_case_reads_prs():
    work = with_case("pm-c1-no-prepatch")
    try:
        result = run_script(work, "--since", "2026-01-01T00:00:00Z")
        check(result.returncode == 0, f"the script exits 0, got {result.returncode}: {result.stderr}")
        state = json.loads(result.stdout or "{}")
        prs = {p["number"]: p for p in state.get("prs", [])}
        check(12 in prs and prs[12]["headRefName"] == "issue/11-json-flag" and prs[12]["isDraft"] is True,
              f"the open draft PR #12 is in the state, got {state.get('prs')}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_fake_refuses_other_graphql():
    work = with_case("pm-triage")
    try:
        env = dict(os.environ, IFE_TRACKER=os.path.join(work, "tracker.json"), IFE_GH_LOG=os.path.join(work, "gh-log.jsonl"))
        fake = os.path.join(FAKE_DIR, "gh")
        result = subprocess.run([sys.executable, fake, "api", "graphql", "-f", "query=mutation { addLabelsToLabelable }"],
                                capture_output=True, text=True, env=env, cwd=work)
        check(result.returncode == 1 and "not supported by the eval fake" in result.stderr,
              f"a GraphQL mutation exits 1 and names the fake, got {result.returncode} {result.stderr!r}")
        result = subprocess.run([sys.executable, fake, "api", "graphql", "-f", "query=query { viewer { login } }"],
                                capture_output=True, text=True, env=env, cwd=work)
        check(result.returncode == 1, f"a GraphQL query other than TrackerState exits 1, got {result.returncode}")
        kinds = [e["kind"] for e in read_log(work)]
        check(kinds == ["error", "error"], f"the log records both refusals, got {kinds}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_cli_failure_exit_code():
    work = tempfile.mkdtemp(prefix="tracker-state-test-")
    try:
        # No tracker file: the fake exits 1, so the script must report a forge failure.
        env = dict(os.environ, PATH=FAKE_DIR + os.pathsep + os.environ.get("PATH", ""),
                   IFE_TRACKER=os.path.join(work, "missing.json"))
        env.pop("IFE_GH_LOG", None)
        result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env, cwd=work)
        check(result.returncode == 1, f"a failed forge call exits 1, got {result.returncode}: {result.stderr}")
        check("gh api graphql" in result.stderr, f"the error names the failed call, got {result.stderr!r}")
        result = subprocess.run([sys.executable, SCRIPT, "--since", "yesterday"], capture_output=True, text=True,
                                env=env, cwd=work)
        check(result.returncode == 2, f"a --since that is not ISO 8601 exits 2, got {result.returncode}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


for test in (test_github_pages_until_both_lists_end, test_github_maps_one_pass_for_stage_a,
             test_github_without_since_reads_untriaged_threads_only, test_github_repo_flag_and_errors,
             test_gitea_pages_and_maps, test_gitea_without_since_reads_each_untriaged_thread,
             test_end_to_end_split_status_case, test_end_to_end_triage_case,
             test_end_to_end_cross_check_case_reads_prs, test_fake_refuses_other_graphql, test_cli_failure_exit_code):
    try:
        test()
    except Exception as error:  # A crash in one test must not hide the others.
        failures.append(f"{test.__name__} crashed: {error!r}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("tracker-state: all cases pass")
