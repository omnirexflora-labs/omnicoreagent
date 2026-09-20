#!/usr/bin/env python3
"""P5: the steward's own failures are work — triage clusters them and schedules them.

Run on the server (see scenario_p1.py for the environment):

    /opt/steward/run_scenario.sh scenario_p5.py

What it asserts, in order:
  1. a triage run reads the steward's own failed runs (its telemetry) and
     the repository's issues, clusters them by cause, and schedules each
     cause as one work item (a background task of the steward's); the
     items exist, each names its cause and a title;
  2. a second triage run — the flood — schedules nothing new: every cause
     it finds already has its item; the set of items is unchanged;
  3. the runtime itself refuses a duplicate task id.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error

from scenario_p1 import api, check

TASK_ID = "p5-triage"
PREFIX = "steward-work-"
QUERY = (
    "Triage. Read your own failed runs (list_failed_runs) and the repository's open issues "
    "(list_issues, at most ten). Cluster everything by cause — runs that failed the same way are "
    "one cause — and read list_work_items before you schedule anything. Schedule one work item per "
    "cause with schedule_work_item (at most five in this run), each with a query a future run of "
    "yours can act on alone. Report the items you scheduled and the ones that already existed. "
    "Do not write anything to GitHub in this run."
)


def work_items() -> dict[str, dict]:
    listing = api("GET", "/background/tasks")
    tasks = listing.get("tasks", listing) if isinstance(listing, dict) else listing
    return {
        t["task_id"]: t for t in (tasks or []) if str(t.get("task_id", "")).startswith(PREFIX)
    }


def ensure_task() -> None:
    existing = api("GET", "/background/tasks")
    tasks = existing.get("tasks", existing) if isinstance(existing, dict) else existing
    if any(task.get("task_id") == TASK_ID for task in tasks):
        api("DELETE", f"/background/tasks/{TASK_ID}")
    api("POST", "/background/tasks", {
        "task_id": TASK_ID,
        "agent_id": "steward",
        "query": QUERY,
        "schedule": {"type": "manual"},
        "timeout_seconds": 900,
    })


def tool_calls_of(run_id: str) -> list[str]:
    trace = api("GET", f"/telemetry/runs/{run_id}/trace")
    body = trace.get("trace", trace)
    return [
        (e.get("input") or {}).get("tool_name") or (e.get("metadata") or {}).get("tool_name") or ""
        for e in body.get("events", [])
        if e.get("event_type") in {"tool_call", "local_tool_call"}
    ]


def triage(label: str) -> tuple[dict, list[str]]:
    started = time.time()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": True}, timeout=960)
    check(run.get("status") == "completed", f"{label}: run {run.get('run_id')} is {run.get('status')} in {time.time() - started:.0f}s"
          + (f" — error: {run.get('error')}" if run.get("error") else ""))
    return run, tool_calls_of(run["run_id"])


def main(*, reset: bool) -> None:
    print("P5.1 triage schedules one item per cause")
    if reset:
        for task_id in work_items():
            api("DELETE", f"/background/tasks/{task_id}")
        print("       cleared the existing work items")
    ensure_task()
    before = work_items()

    first, calls = triage("first triage")
    check("list_failed_runs" in calls, "it read its own failed runs")
    check("list_work_items" in calls, "it read the existing work items first")
    after_first = work_items()
    new = {k: v for k, v in after_first.items() if k not in before}
    check(len(after_first) >= 1, f"{len(after_first)} work item(s) exist, {len(new)} scheduled by this run")
    for task_id, task in list(after_first.items())[:5]:
        meta = task.get("metadata") or {}
        check(bool(meta.get("cause")) and bool(meta.get("title")), f"{task_id}: {str(meta.get('title'))[:70]!r}")
    print(f"       answer: {str(first.get('result_preview'))[:200]!r}")

    print("P5.2 the flood: a second triage schedules nothing new")
    second, calls = triage("second triage")
    after_second = work_items()
    check(set(after_second) == set(after_first),
          f"the set of work items is unchanged ({len(after_second)} items)")
    print(f"       answer: {str(second.get('result_preview'))[:200]!r}")

    print("P5.3 the runtime refuses a duplicate task id")
    sample = next(iter(after_first))
    try:
        api("POST", "/background/tasks", {"task_id": sample, "agent_id": "steward", "query": "again",
                                          "schedule": {"type": "manual"}})
        check(False, f"a second task named {sample} was accepted")
    except SystemExit as refused:
        check("409" in str(refused) or "400" in str(refused) or "exists" in str(refused).lower(),
              f"refused: {str(refused)[:80]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete existing work items first")
    arguments = parser.parse_args()
    main(reset=arguments.reset)
    print("done")
