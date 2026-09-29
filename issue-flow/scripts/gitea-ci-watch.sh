#!/usr/bin/env bash
# Watch Gitea Actions for one commit until its CI is terminal; print one verdict line.
#
#   bash <pluginRoot>/scripts/gitea-ci-watch.sh <sha>
#
# Run it from inside the repository checkout (tea fills {owner}/{repo} from the
# remote), with run_in_background: true — see references/forge.md, "Gitea: watch
# CI to a terminal verdict". It is a script, not an inline loop, because the
# worktree guard of Claude Code refuses the inline loop in an isolated worker
# (tea inside $(…) and a runtime-built URL), and allows `bash <script>`.
#
# Verdicts, one line on stdout:
#   success            every run for the commit completed success or skipped
#   failure            every run completed, at least one did not succeed
#   no-run-registered  no run appeared for the commit — NOT a pass
#   timed-out          runs still going when the watch gave up — NOT a pass
#   watch-error        the request failed or returned something that is not a
#                      run list (exit 1) — never a pass
#
# Tunable for tests and slow runners; never lower the none-limit in production:
#   IFW_TEA (tea) IFW_NONE_LIMIT (6) IFW_NONE_SLEEP (10) IFW_RUN_SLEEP (30) IFW_MAX (60)

set -u

sha="${1:-}"
if ! printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "usage: gitea-ci-watch.sh <full 40-character commit sha>" >&2
  exit 2
fi

tea="${IFW_TEA:-tea}"
none_limit="${IFW_NONE_LIMIT:-6}"
none_sleep="${IFW_NONE_SLEEP:-10}"
run_sleep="${IFW_RUN_SLEEP:-30}"
max="${IFW_MAX:-60}"

# The aggregate over every workflow run for the commit. An object without a run
# list (an API error such as {"message": "not found"}) is an error, not "no runs":
# reading it as an empty list would report no-run-registered for a failed request.
# A response that is not an object makes jq itself fail, which is also an error.
verdict_filter='
  (.workflow_runs // .runs) as $r
  | if ($r | type) != "array"                 then "error"
    elif ($r | length) == 0                   then "pending:none"
    elif any($r[]; .status != "completed")    then "pending:running"
    elif all($r[]; .conclusion == "success" or .conclusion == "skipped")
                                              then "success"
    else "failure" end'

none=0
i=0
while [ "$i" -lt "$max" ]; do
  i=$((i + 1))
  body=$("$tea" api "/repos/{owner}/{repo}/actions/runs?head_sha=$sha") || {
    echo "watch-error"
    exit 1
  }
  v=$(printf '%s' "$body" | jq -r "$verdict_filter" 2>/dev/null)
  case "$v" in
    pending:none)
      none=$((none + 1))
      if [ "$none" -ge "$none_limit" ]; then
        echo "no-run-registered"
        exit 0
      fi
      sleep "$none_sleep"
      ;;
    pending:running)
      sleep "$run_sleep"
      ;;
    success | failure)
      echo "$v"
      exit 0
      ;;
    *)
      echo "watch-error"
      printf '%s\n' "$body" | head -c 300 >&2
      exit 1
      ;;
  esac
done
echo "timed-out"
exit 0
