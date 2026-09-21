#!/usr/bin/env python3
"""P3: the steward fixes the failure behind a person's approval, and never pushes twice.

Run on the server (see scenario_p1.py for the environment):

    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p3.py          # fix, approve, PR
    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p3.py --kill   # kill it after the push, before the PR

What it asserts, in order:
  1. a task naming the failure runs until it asks: each GitHub write
     (create_branch, push_files, create_pull_request) pauses the run; this
     script approves it over HTTP as a person would and resumes the run; at
     the end the branch is one commit ahead of main and one pull request
     exists for it, its body naming the run;
  2. (--kill) the serve container is killed the moment the push has landed
     and before the pull request exists; the run resumes and finishes; the
     branch is still exactly one commit ahead of main (the push was not
     repeated) and exactly one pull request exists.

Every approval and the pull request are real: the branch and the PR stay on
the repository for a person to review.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from scenario_p1 import api, check, ssh

TASK_ID = "p3-fix-behind-approval"
REPOSITORY = "omnirexflora-labs/omnicoreagent"
BRANCH = "refactor/native-tool-runtime"
TEST = "tests/test_llm.py::test_cookbook_luna_default_and_explicit_reasoning_override"
QUERY = (
    f"On branch {BRANCH} of the repository you steward, the test {TEST} fails with "
    "`ModuleNotFoundError: No module named 'cookbook'` whenever it runs outside the full suite "
    "(cookbook/ is a directory of the repository, not an installed package). Fix it: delegate the fix "
    "to one worker named `fix` that makes the smallest change that makes the test pass alone and with "
    "its file, verifies both in its sandbox, and writes the new file contents and a commit message to "
    "its output path. Then, as the steward, create the branch, push the change in one commit, and open "
    f"a pull request against {BRANCH} (the base is {BRANCH}, not main) whose body links this run. "
    "Each write asks a person; wait for the answer."
)
WRITES = ("create_branch", "push_files", "create_pull_request")


def github(path: str) -> dict | list:
    request = urllib.request.Request(
        f"https://api.github.com{path}", headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


REHEARSAL = (
    " A pull request for this fix may already be open from an earlier rehearsal; this run is its "
    "own rehearsal and must still create its own branch, push its own commit, and open its own "
    "pull request rather than pointing at the existing one."
)


def ensure_task(*, rehearsal: bool = False) -> None:
    existing = api("GET", "/background/tasks")
    tasks = existing.get("tasks", existing) if isinstance(existing, dict) else existing
    if any(task.get("task_id") == TASK_ID for task in tasks):
        stale = latest_run()
        if stale.get("status") in {"queued", "claimed", "running", "retrying", "awaiting_approval"}:
            try:
                api("POST", f"/background/runs/{stale['run_id']}/cancel")
            except SystemExit:
                pass
        api("DELETE", f"/background/tasks/{TASK_ID}")
    api("POST", "/background/tasks", {
        "task_id": TASK_ID,
        "agent_id": "steward",
        "query": QUERY + (REHEARSAL if rehearsal else ""),
        "schedule": {"type": "manual"},
        # A fix is clone, install, change, test, and again; then three pauses.
        "timeout_seconds": 2400,
    })


def latest_run() -> dict:
    try:
        status = api("GET", f"/background/tasks/{TASK_ID}/status")
    except (SystemExit, urllib.error.URLError, ConnectionError):
        return {}
    return status.get("latest_run") or status.get("last_run") or {}


def approve_pending(run_id: str, approved: list[dict]) -> None:
    """Approve every pending write the run asks for, as a person would: read
    it, check the branch is the steward's, decide, then resume the run."""
    record = api("GET", f"/runs/{run_id}")
    pending = [a for a in record.get("approvals", []) if a.get("status") == "pending"]
    for approval in pending:
        tool = approval.get("tool_name")
        arguments = approval.get("arguments") or {}
        branch = arguments.get("branch") or arguments.get("head") or ""
        check(tool in WRITES, f"asked for {tool} ({approval.get('capability')})")
        if tool in ("create_branch", "push_files"):
            check(str(branch).startswith("steward/"), f"on branch {branch}")
        api("POST", f"/runs/{run_id}/approvals/{approval['approval_id']}",
            {"decision": "approve", "approver": "scenario_p3"})
        approved.append({"tool": tool, "branch": branch, "arguments": arguments})
        print(f"       approved {tool} {branch}")
    if pending:
        api("POST", f"/background/runs/{run_id}/resume")


def drive(run_id: str, *, kill_after_push: bool, deadline_seconds: int = 1500) -> tuple[dict, list[dict], bool]:
    """Wait on the run; approve each pause; optionally kill the server right
    after the push lands. Returns the final run, the approvals, and whether
    the kill happened."""
    approved: list[dict] = []
    killed = False
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        run = latest_run()
        if run.get("run_id") == run_id:
            if run.get("status") in {"completed", "failed", "cancelled"}:
                return run, approved, killed
            if run.get("status") == "awaiting_approval":
                approve_pending(run_id, approved)
        if kill_after_push and not killed and any(a["tool"] == "push_files" for a in approved):
            if push_landed(run_id):
                print("       the push has landed; killing the serve container now")
                print("       " + ssh("docker kill steward-serve && docker start steward-serve && echo restarted").strip())
                killed = True
        time.sleep(2 if kill_after_push else 4)
    return latest_run(), approved, killed


def push_landed(run_id: str) -> bool:
    try:
        trace = api("GET", f"/telemetry/runs/{run_id}/trace")
    except (SystemExit, urllib.error.URLError, ConnectionError):
        return False
    body = trace.get("trace", trace)
    return any(
        event.get("event_type") == "mcp_tool_result" and "push_files" in json.dumps(event)
        for event in body.get("events", [])
    )


def assert_one_push_one_pr(branch: str, run_id: str) -> None:
    compare = github(f"/repos/{REPOSITORY}/compare/{BRANCH}...{branch}")
    check(compare.get("ahead_by") == 1, f"branch {branch} is {compare.get('ahead_by')} commit ahead of {BRANCH}")
    owner = REPOSITORY.split("/")[0]
    pulls = github(f"/repos/{REPOSITORY}/pulls?state=all&head={owner}:{branch}")
    check(len(pulls) == 1, f"{len(pulls)} pull request(s) for {branch}")
    pull = pulls[0]
    check(run_id in (pull.get("body") or ""), f"PR #{pull.get('number')} links the run: {pull.get('html_url')}")


def run_scenario(*, kill: bool) -> None:
    print("P3.2 killed after the push, before the pull request" if kill else "P3.1 a fix behind approvals")
    ensure_task(rehearsal=kill)
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": False})
    run_id = run["run_id"]
    print(f"       run {run_id} queued")
    final, approved, killed = drive(run_id, kill_after_push=kill)
    check(final.get("status") == "completed", f"run {run_id} is {final.get('status')}"
          + (f" — error: {final.get('error')}" if final.get("error") else ""))
    tools = [a["tool"] for a in approved]
    check(tools.count("push_files") == 1 and tools.count("create_pull_request") == 1,
          f"approvals in order: {tools}")
    branch = next(a["branch"] for a in approved if a["tool"] == "push_files")
    if kill:
        check(killed, "the container was killed after the push")
        record = api("GET", f"/runs/{run_id}")
        check(len(record.get("trace_ids") or []) >= 2, f"the run joins {len(record.get('trace_ids') or [])} trace segments")
    assert_one_push_one_pr(branch, run_id)
    print(f"       answer: {str(final.get('result_preview'))[:240]!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kill", action="store_true", help="kill the server after the push, before the PR")
    arguments = parser.parse_args()
    run_scenario(kill=arguments.kill)
    print("done")
