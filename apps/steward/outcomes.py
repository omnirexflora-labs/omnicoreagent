#!/usr/bin/env python3
"""What the steward's work turned out to be worth, read from GitHub.

Run on the server, hourly from cron (see README):

    /opt/steward/run_scenario.sh outcomes.py           # record what is decided
    STEWARD_SSH= STEWARD_TOKEN=... python3 outcomes.py --dry-run

A run that opened a pull request is not finished when it ends: the pull
request is merged, or closed without merging, hours or days later. That
verdict is the run's reward — the evaluative feedback a trainer or an
evaluator needs, and what rLLM's real-time RL centres against a batch mean.
This reads each completed run's trace for the pull request it opened, asks
GitHub what became of it, and records it on the run
(``POST /runs/{run_id}/outcome``):

    merged            reward 1.0
    closed, unmerged  reward 0.0
    still open        nothing yet; the next hour asks again

An outcome already recorded for that pull request is not recorded twice.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request

from scenario_p1 import api

REPOSITORY = "omnirexflora-labs/omnicoreagent"
PULL_REQUEST = re.compile(rf"github\.com/{REPOSITORY}/pull/(\d+)")


def github(path: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com{path}", headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def pull_requests_of(run: dict) -> set[int]:
    """The pull requests a run opened, from its answer and its trace."""
    found = {int(number) for number in PULL_REQUEST.findall(str(run.get("result_preview") or ""))}
    try:
        trace = api("GET", f"/telemetry/runs/{run['run_id']}/trace", timeout=120)
    except SystemExit:
        return found
    body = trace.get("trace", trace)
    for event in body.get("events", []):
        if event.get("event_type") != "mcp_tool_result":
            continue
        output = json.dumps(event.get("output") or {})
        if '"create_pull_request"' in output or "/pull/" in output:
            found.update(int(number) for number in PULL_REQUEST.findall(output))
    return found


def outcome_of(number: int) -> tuple[float, str] | None:
    pull = github(f"/repos/{REPOSITORY}/pulls/{number}")
    if pull.get("merged"):
        return 1.0, "merged"
    if pull.get("state") == "closed":
        return 0.0, "closed_unmerged"
    return None


def already_recorded(run_id: str, number: int) -> bool:
    record = api("GET", f"/runs/{run_id}", timeout=60)
    return any(
        (outcome.get("detail") or {}).get("pull_request") == number
        for outcome in record.get("outcomes") or []
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="say what would be recorded")
    parser.add_argument("--limit", type=int, default=200, help="how many runs to look at")
    arguments = parser.parse_args()

    listing = api("GET", f"/background/runs?limit={arguments.limit}", timeout=120)
    runs = listing.get("runs", listing) if isinstance(listing, dict) else listing
    recorded = 0
    for run in runs:
        if run.get("status") != "completed":
            continue
        for number in sorted(pull_requests_of(run)):
            verdict = outcome_of(number)
            if verdict is None:
                print(f"       #{number} still open ({run['run_id']})")
                continue
            reward, label = verdict
            if already_recorded(run["run_id"], number):
                continue
            print(f"  ok   #{number} {label} -> {run['run_id']} reward {reward}")
            if arguments.dry_run:
                continue
            api(
                "POST",
                f"/runs/{run['run_id']}/outcome",
                {
                    "source": "github",
                    "reward": reward,
                    "label": label,
                    "detail": {"pull_request": number, "repository": REPOSITORY},
                },
                timeout=60,
            )
            recorded += 1
    print(f"done: {recorded} outcome(s) recorded")


if __name__ == "__main__":
    main()
