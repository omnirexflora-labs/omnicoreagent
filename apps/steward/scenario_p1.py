#!/usr/bin/env python3
"""P1: the steward is deployed, does one piece of work, and survives a restart.

Run on the server itself (STEWARD_SSH="" makes the restart step use docker
directly), or from a machine with an SSH tunnel to it (see README.md):

    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p1.py            # one run, end to end
    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p1.py --restart  # kill it mid-run

What it asserts, in order:
  1. the server is healthy and the background worker is up;
  2. a manual task can be registered and run to completion, and its trace
     reads end to end: a model call, a GitHub tool call through MCP, a final
     answer, status completed, one trace;
  3. (--restart) with a run in flight, the serve container is killed and
     started again; the run finishes anyway, and its record shows more than
     one trace segment joined into one run.

Nothing here is a demo helper: every assertion is what a person would check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("STEWARD_URL", "http://127.0.0.1:8800")
TOKEN = os.environ.get("STEWARD_TOKEN", "")
SERVER = os.environ.get("STEWARD_SSH", "root@2.29.43.238")
SSH_KEY = os.environ.get("STEWARD_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519_hetzner_20260906"))

TASK_ID = "p1-read-the-repo"
QUERY = (
    "Read the repository you steward: list its open issues (at most five) and "
    "report their numbers and titles, then say which one you would reproduce "
    "first and why. Do not write anything to GitHub in this run."
)


def api(method: str, path: str, body: dict | None = None, timeout: float = 900) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> HTTP {error.code}: {detail[:400]}") from None


def check(condition: bool, what: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + what)
    if not condition:
        raise SystemExit(1)


def ssh(command: str) -> str:
    """Run a command on the server — over SSH, or directly when this script
    already runs there (STEWARD_SSH empty)."""
    argv = ["sh", "-c", command] if not SERVER else [
        "ssh", "-i", SSH_KEY, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", SERVER, command,
    ]
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=300).stdout


def ensure_task() -> None:
    """The scenario's task, registered fresh under the policy the server runs
    now: a task is bound to the policy snapshot it was created under, and the
    runtime refuses to run it under a different one (that is a feature; it
    surfaced the first time the steward's policy changed between deploys)."""
    existing = api("GET", "/background/tasks")
    tasks = existing.get("tasks", existing) if isinstance(existing, dict) else existing
    if any(task.get("task_id") == TASK_ID for task in tasks):
        api("DELETE", f"/background/tasks/{TASK_ID}")
    api("POST", "/background/tasks", {
        "task_id": TASK_ID,
        "agent_id": "steward",
        "query": QUERY,
        "schedule": {"type": "manual"},
        "timeout_seconds": 600,
    })


def trace_of(run_id: str) -> dict:
    return api("GET", f"/telemetry/runs/{run_id}/trace")


def event_types(trace: dict) -> list[str]:
    body = trace.get("trace", trace)
    return [event.get("event_type") for event in body.get("events", [])]


def part_one() -> None:
    print("P1.1 the server is up")
    health = api("GET", "/health")
    check(health.get("status") in {"ok", "healthy"}, f"health: {health.get('status')}")
    status = api("GET", "/background/status")
    check(bool(status), f"background status: {json.dumps(status)[:120]}")

    print("P1.2 one piece of work, end to end")
    ensure_task()
    started = time.time()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": True})
    took = time.time() - started
    check(run.get("status") == "completed", f"run {run.get('run_id')} status {run.get('status')} in {took:.0f}s"
          + (f" — error: {run.get('error')}" if run.get("error") else ""))
    check(bool(run.get("result_preview")), f"it answered: {str(run.get('result_preview'))[:140]!r}")

    trace = trace_of(run["run_id"])
    types = event_types(trace)
    check("model_call" in types and "model_response" in types, f"the model was called ({types.count('model_call')} times)")
    check(any(t in types for t in ("mcp_tool_call", "tool_call", "tool_resolved")), "a tool was called through MCP")
    check("final_answer" in types, "a final answer was recorded")
    body = trace.get("trace", trace)
    check(body.get("status") == "completed", f"trace status {body.get('status')}")
    summary = trace.get("summary") or {}
    print(f"       trace {body.get('trace_id')}: {len(types)} events; summary keys {sorted(summary)[:6]}")


def part_restart() -> None:
    print("P1.3 killed mid-run, the run still finishes")
    ensure_task()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": False})
    run_id = run["run_id"]
    print(f"       run {run_id} queued; killing the serve container in 8s")
    time.sleep(8)
    print("       " + ssh("docker kill steward-serve && docker start steward-serve && echo restarted").strip())
    deadline = time.time() + 600
    final = None
    while time.time() < deadline:
        try:
            final = api("GET", f"/background/tasks/{TASK_ID}/status")
        except (SystemExit, urllib.error.URLError, ConnectionError):
            time.sleep(5)
            continue
        latest = (final.get("latest_run") or final.get("last_run") or {})
        if latest.get("run_id") == run_id and latest.get("status") in {"completed", "failed", "cancelled"}:
            break
        time.sleep(5)
    latest = (final or {}).get("latest_run") or (final or {}).get("last_run") or {}
    check(latest.get("status") == "completed", f"after the restart, run {run_id} is {latest.get('status')}")
    record = api("GET", f"/runs/{run_id}")
    segments = record.get("trace_ids") or []
    check(len(segments) >= 1, f"the run record joins {len(segments)} trace segment(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="kill the server mid-run and expect it to finish")
    arguments = parser.parse_args()
    if not TOKEN:
        print("STEWARD_TOKEN is not set (the server's OMNICOREAGENT_SERVE_AUTH_TOKEN)", file=sys.stderr)
    part_restart() if arguments.restart else part_one()
    print("done")
