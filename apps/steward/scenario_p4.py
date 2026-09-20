#!/usr/bin/env python3
"""P4: budgets in dollars — a run stops at its cap and waits, a top-up lets it
finish, the bill matches the ledger, and two processes cannot overspend.

Run on the server with the proving deployment's request budget lowered so
that one ordinary piece of work runs out mid-way:

    STEWARD_REQUEST_USD=0.08  in /opt/steward/.env, then redeploy, then
    /opt/steward/run_scenario.sh scenario_p4.py                 # pause, top up, finish, bill
    /opt/steward/run_scenario.sh scenario_p4.py --two-workers   # compare-and-swap across processes

What it asserts, in order:
  1. a run that costs more than its request budget goes to `awaiting_budget`
     (not completed, not failed), saying which budget and how much more;
     `GET /runs/{id}/budget` shows the request budget spent to its limit;
     a grant over HTTP and a resume let it finish; what the ledger says the
     run spent equals what its traces say its model calls cost, and the
     application's day counter grew by the same amount;
  2. (--two-workers) two processes charging the same Postgres-backed budget
     key at once: every charge lands exactly once (the compare-and-swap
     holds across processes), none is lost or doubled.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
import urllib.error

from scenario_p1 import QUERY as READ_THE_REPO, api, check, ssh

TASK_ID = "p4-budget-pause"
EXPECTED_REQUEST_USD = 0.08


def ensure_task() -> None:
    existing = api("GET", "/background/tasks")
    tasks = existing.get("tasks", existing) if isinstance(existing, dict) else existing
    if any(task.get("task_id") == TASK_ID for task in tasks):
        api("DELETE", f"/background/tasks/{TASK_ID}")
    api("POST", "/background/tasks", {
        "task_id": TASK_ID,
        "agent_id": "steward",
        "query": READ_THE_REPO,
        "schedule": {"type": "manual"},
        "timeout_seconds": 900,
    })


def latest_run() -> dict:
    try:
        status = api("GET", f"/background/tasks/{TASK_ID}/status")
    except (SystemExit, urllib.error.URLError, ConnectionError):
        return {}
    return status.get("latest_run") or status.get("last_run") or {}


def wait_for(run_id: str, statuses: set[str], deadline_seconds: int = 900) -> dict:
    deadline = time.time() + deadline_seconds
    run: dict = {}
    while time.time() < deadline:
        run = latest_run()
        if run.get("run_id") == run_id and run.get("status") in statuses:
            return run
        time.sleep(4)
    return run


def budget(run_id: str, scope: str, meter: str = "model_cost_usd") -> dict:
    entries = api("GET", f"/runs/{run_id}/budget").get("budgets", [])
    return next((e for e in entries if e["scope"] == scope and e["meter"] == meter), {})


def traced_cost(run_id: str) -> tuple[float, int]:
    """What the run's model calls cost according to its traces (all segments)."""
    family = api("GET", f"/telemetry/runs/{run_id}/family")
    traces = family.get("traces", family) if isinstance(family, dict) else family
    cost, calls = 0.0, 0
    for trace in traces or []:
        if "events" not in trace and trace.get("trace_id"):
            trace = api("GET", f"/telemetry/traces/{trace['trace_id']}")
            trace = trace.get("trace", trace)
        for event in trace.get("events", []):
            if event.get("event_type") == "model_response":
                calls += 1
                figure = ((event.get("metadata") or {}).get("model_call") or {}).get("estimated_cost_usd")
                if isinstance(figure, (int, float)):
                    cost += figure
    return cost, calls


def part_one() -> None:
    print("P4.1 a run stops at its cap, is topped up, finishes, and the bill matches")
    ensure_task()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": False})
    run_id = run["run_id"]
    # The application's day counter before this run spends anything.
    time.sleep(6)
    limit = budget(run_id, "request")
    check(limit.get("limit") == EXPECTED_REQUEST_USD,
          f"the request budget is ${limit.get('limit')} (set STEWARD_REQUEST_USD={EXPECTED_REQUEST_USD} and redeploy)")
    day_before = budget(run_id, "application").get("spent", 0.0) - limit.get("spent", 0.0)

    waiting = wait_for(run_id, {"awaiting_budget", "completed", "failed", "cancelled", "timeout"})
    check(waiting.get("status") == "awaiting_budget",
          f"run {run_id} is {waiting.get('status')}: {str(waiting.get('result_preview'))[:100]!r}")
    record = api("GET", f"/runs/{run_id}")
    pending = [r for r in record.get("budget_requests", []) if r.get("status") == "pending"]
    check(len(pending) == 1 and pending[0].get("meter") == "model_cost_usd",
          f"it asks for {pending[0].get('scope') if pending else '?'} model_cost_usd, {pending[0].get('shortfall') if pending else '?'} more")
    at_cap = budget(run_id, "request")
    check(at_cap.get("spent", 0) <= EXPECTED_REQUEST_USD + 1e-9 and at_cap.get("remaining", 1) < 0.02,
          f"the request budget is spent to its cap: {at_cap.get('spent')} of {at_cap.get('limit')}")

    api("POST", f"/runs/{run_id}/budget", {"decision": "grant", "amount": 0.60, "approver": "scenario_p4",
                                            "note": "finish this one"})
    api("POST", f"/background/runs/{run_id}/resume")
    print("       granted $0.60 and resumed")
    final = wait_for(run_id, {"completed", "failed", "cancelled", "timeout"})
    check(final.get("status") == "completed", f"after the top-up, run {run_id} is {final.get('status')}"
          + (f" — error: {final.get('error')}" if final.get("error") else ""))

    settled = budget(run_id, "request")
    cost, calls = traced_cost(run_id)
    check(abs(settled.get("spent", 0) - cost) < 0.005,
          f"the ledger says ${settled.get('spent'):.4f}; the traces say ${cost:.4f} over {calls} model calls")
    day_after = budget(run_id, "application").get("spent", 0.0)
    check(abs((day_after - day_before) - cost) < 0.005,
          f"the application's day counter grew by ${day_after - day_before:.4f}")
    segments = api("GET", f"/runs/{run_id}").get("trace_ids") or []
    print(f"       {len(segments)} trace segment(s); answer: {str(final.get('result_preview'))[:120]!r}")


CAS_PROGRAM = r'''
import asyncio, os, sys, time, uuid
from multiprocessing import Process
from omnicoreagent import MemoryRouter
from omnicoreagent.core.budgets import BudgetLedger

KEY = sys.argv[1]; N = int(sys.argv[2]); AMOUNT = 0.001

async def spend(n):
    ledger = BudgetLedger(MemoryRouter("sql"))
    await asyncio.gather(*[ledger.charge_many(KEY, [("model_cost_usd", AMOUNT, None)]) for _ in range(n)])

def worker():
    asyncio.run(spend(N))

if __name__ == "__main__":
    started = time.monotonic()
    procs = [Process(target=worker) for _ in range(2)]
    for p in procs: p.start()
    for p in procs: p.join()
    async def read():
        ledger = BudgetLedger(MemoryRouter("sql"))
        usage = await ledger.usage(KEY)
        await ledger.delete(KEY)
        return usage
    usage = asyncio.run(read())
    exits = ",".join(str(p.exitcode) for p in procs)
    print(f"charges={2*N} expected={2*N*AMOUNT:.3f} ledger={usage.get('model_cost_usd', 0):.6f} exits={exits} seconds={time.monotonic()-started:.1f}")
'''


def part_two_workers() -> None:
    print("P4.2 two processes on one budget key, on Postgres")
    key = f"application:p4-cas-{int(time.time())}:total"
    n = 300
    out = ssh(
        "docker exec -i steward-serve python - " + shlex.quote(key) + f" {n} <<'PY'\n{CAS_PROGRAM}\nPY"
    ).strip().splitlines()[-1]
    print("       " + out)
    fields = dict(part.split("=", 1) for part in out.split())
    check(fields.get("exits") == "0,0", f"both processes finished (exit codes {fields.get('exits')})")
    check(abs(float(fields["ledger"]) - float(fields["expected"])) < 1e-9,
          f"every charge landed once: {fields['ledger']} of {fields['expected']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--two-workers", action="store_true", help="two processes charge one key at once")
    arguments = parser.parse_args()
    part_two_workers() if arguments.two_workers else part_one()
    print("done")
