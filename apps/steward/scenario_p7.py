#!/usr/bin/env python3
"""P7: a week unattended — the steward on its schedule, measured every hour.

Run on the server (see scenario_p1.py for the environment):

    /opt/steward/run_scenario.sh scenario_p7.py --start    # register the schedules, once
    python3 apps/steward/scenario_p7.py --status            # any time: runs, spend, growth

--start registers two scheduled tasks and leaves them running:
  - p7-read-the-repo: every 12 hours, the P1 piece of work;
  - p7-triage: every 6 hours, the P5 triage (its own failures as work).
The hourly measurement is `measure.sh`, installed in the server's crontab,
appending one line to /opt/steward/logs/p7.csv: container memory and pids,
the workspace, Postgres and Redis sizes, the runs by status, and the
application's spend for the day.

--status reads the tasks, their runs by status, the last measurement lines,
and what grew since the first line. Anything that grows without bound, leaks,
or drifts is a runtime bug to fix.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter

from scenario_p1 import QUERY as READ_THE_REPO, api, check
from scenario_p5 import QUERY as TRIAGE

TASKS = {
    "p7-read-the-repo": (READ_THE_REPO, 12 * 3600),
    "p7-triage": (TRIAGE, 6 * 3600),
}
CSV = "/opt/steward/logs/p7.csv"


def start() -> None:
    print("P7 start: the steward on its schedule")
    existing = api("GET", "/background/tasks")
    known = {t["task_id"] for t in (existing.get("tasks", existing) if isinstance(existing, dict) else existing)}
    for task_id, (query, seconds) in TASKS.items():
        if task_id in known:
            print(f"       {task_id} already registered")
            continue
        api("POST", "/background/tasks", {
            "task_id": task_id,
            "agent_id": "steward",
            "query": query,
            "schedule": {"type": "interval", "seconds": seconds, "jitter_seconds": 300},
            "timeout_seconds": 1800,
            "retry_policy": {"max_retries": 1, "initial_delay_seconds": 60},
            "overlap_policy": "skip_if_running",
        })
        check(True, f"{task_id} every {seconds // 3600} h")
    status = api("GET", "/background/status")
    print("       background:", json.dumps(status)[:160])


def status() -> None:
    print("P7 status")
    listing = api("GET", "/background/runs")
    runs = listing.get("runs", listing) if isinstance(listing, dict) else listing
    for task_id in TASKS:
        mine = [r for r in runs if r.get("task_id") == task_id]
        by_status = Counter(r.get("status") for r in mine)
        print(f"       {task_id}: {len(mine)} runs {dict(by_status)}")
        failed = [r for r in mine if r.get("status") in {"failed", "timeout"}]
        for r in failed[-3:]:
            print(f"         {r['run_id']} {r['status']}: {str(r.get('error'))[:100]}")
    try:
        lines = open(CSV).read().strip().splitlines()
    except OSError:
        print("       no measurements yet"); return
    header, rows = lines[0].split(","), [l.split(",") for l in lines[1:]]
    print(f"       {len(rows)} hourly measurements; columns: {', '.join(header)}")
    if rows:
        first, last = dict(zip(header, rows[0])), dict(zip(header, rows[-1]))
        for key in header[1:]:
            print(f"         {key}: {first.get(key)} -> {last.get(key)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--status", action="store_true")
    arguments = parser.parse_args()
    if arguments.start:
        start()
    else:
        status()
    print("done")
