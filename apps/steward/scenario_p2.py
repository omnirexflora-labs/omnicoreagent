#!/usr/bin/env python3
"""P2: the steward reproduces a failing test in a sandbox, and survives losing it.

Run on the server (see scenario_p1.py for the environment):

    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p2.py          # reproduce, end to end
    STEWARD_SSH= STEWARD_TOKEN=... python3 apps/steward/scenario_p2.py --lose   # take the sandbox away mid-run

What it asserts, in order:
  1. a task naming one failing test runs to completion: a worker was
     delegated to, a sandbox was opened with the network on, commands ran in
     it, and the steward's answer quotes the real failure
     (`ModuleNotFoundError: No module named 'cookbook'`), one trace family;
  2. (--lose) with the run in flight, the sandbox it opened is killed at the
     provider; the run still completes with the reproduction, and its traces
     show the session closed as lost and a fresh session opened after it.

The test is real: `tests/test_llm.py::test_cookbook_luna_default_and_explicit_
reasoning_override` imports `cookbook`, which is not a package of the
repository, and fails whenever it runs outside the full suite.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error

from scenario_p1 import api, check, ssh

TASK_ID = "p2-reproduce-a-test"
BRANCH = "refactor/native-tool-runtime"
TEST = "tests/test_llm.py::test_cookbook_luna_default_and_explicit_reasoning_override"
QUERY = (
    f"Reproduce this failing test on branch {BRANCH} of the repository you steward: {TEST}. "
    "Delegate the reproduction to one worker named `reproduce`: it clones the branch, installs "
    "with uv, runs that test alone and then its whole file, and writes the exact failing lines "
    "to its output path. Then write output.md in this run's workspace quoting those lines and "
    "saying whether the failure is in the test or in the code under test. Do not write "
    "anything to GitHub in this run."
)


def family_of(run_id: str) -> list[dict]:
    """Every trace linked to the run (the steward's and its workers'), with events."""
    try:
        body = api("GET", f"/telemetry/runs/{run_id}/family")
    except SystemExit:
        return []
    traces = body.get("traces", body) if isinstance(body, dict) else body
    out = []
    for trace in traces or []:
        if "events" not in trace and trace.get("trace_id"):
            trace = api("GET", f"/telemetry/traces/{trace['trace_id']}")
            trace = trace.get("trace", trace)
        out.append(trace)
    return out


def events(traces: list[dict], *types: str) -> list[dict]:
    return [e for t in traces for e in t.get("events", []) if e.get("event_type") in types]


def output_md(run_id: str) -> str:
    """The steward's report, from the server's workspace volume."""
    try:
        return ssh(
            "docker exec steward-serve sh -c "
            f"\"find /app/workspace -path '*{run_id}*' -name output.md -exec cat {{}} + 2>/dev/null | head -60\""
        )
    except Exception as error:  # noqa: BLE001 - the report is a bonus, not an assertion
        return f"(could not read output.md: {error})"


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


def wait_for_run(run_id: str, deadline_seconds: int = 900) -> dict:
    deadline = time.time() + deadline_seconds
    latest: dict = {}
    while time.time() < deadline:
        try:
            status = api("GET", f"/background/tasks/{TASK_ID}/status")
        except (SystemExit, urllib.error.URLError, ConnectionError):
            time.sleep(5)
            continue
        latest = status.get("latest_run") or status.get("last_run") or {}
        if latest.get("run_id") == run_id and latest.get("status") in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(5)
    return latest


def assert_reproduced(run_id: str, latest: dict) -> list[dict]:
    check(latest.get("status") == "completed", f"run {run_id} is {latest.get('status')}"
          + (f" — error: {latest.get('error')}" if latest.get("error") else ""))
    answer = str(latest.get("result_preview") or "")
    check("ModuleNotFoundError" in answer and "cookbook" in answer,
          f"the answer quotes the real failure: {answer[:160]!r}")
    traces = family_of(run_id)
    check(len(traces) >= 2, f"the run's trace family has {len(traces)} traces (steward + worker)")
    check(bool(events(traces, "subagent_spawn")), "a worker was delegated to")
    sessions = events(traces, "sandbox_session_created")
    check(bool(sessions), f"{len(sessions)} sandbox session(s) opened")
    first = sessions[0].get("metadata", {})
    check(first.get("sandbox_provider") == "e2b" and first.get("network") == "allow",
          f"the sandbox is e2b with the network on (ref {first.get('sandbox_ref')})")
    ran = events(traces, "sandbox_exec_completed", "sandbox_exec_failed")
    check(len(ran) >= 3, f"{len(ran)} commands ran in it")
    return traces


def part_one() -> None:
    print("P2.1 reproduce one failing test in a sandbox")
    ensure_task()
    started = time.time()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": True}, timeout=960)
    print(f"       run {run.get('run_id')} finished in {time.time() - started:.0f}s")
    traces = assert_reproduced(run["run_id"], run)
    summary = next((t.get("summary") for t in traces if t.get("summary")), {}) or {}
    print("       cost:", json.dumps((summary.get("model_calls") or {}).get("cost_usd") or summary.get("cost_usd")))
    print("       output.md:\n" + "\n".join("         " + line for line in output_md(run["run_id"]).splitlines()[:30]))


def kill_sandbox(ref: str) -> str:
    """Kill the sandbox at E2B, from inside the serve container (it has the SDK and the key)."""
    program = (
        "import asyncio, sys\n"
        "from e2b import AsyncSandbox\n"
        "async def main():\n"
        "    sandbox = await AsyncSandbox.connect(sys.argv[1])\n"
        "    print('killed' if await sandbox.kill() else 'already gone')\n"
        "asyncio.run(main())\n"
    )
    return ssh(f"docker exec -i steward-serve python - {ref} <<'PY'\n{program}PY").strip()


def part_lose() -> None:
    print("P2.2 the sandbox is taken away mid-run")
    ensure_task()
    run = api("POST", f"/background/tasks/{TASK_ID}/run", {"wait": False})
    run_id = run["run_id"]
    print(f"       run {run_id} queued; waiting for its sandbox")
    ref = None
    deadline = time.time() + 600
    while time.time() < deadline and ref is None:
        for event in events(family_of(run_id), "sandbox_session_created"):
            ref = event.get("metadata", {}).get("sandbox_ref")
            if ref:
                break
        if ref is None:
            time.sleep(3)
    check(ref is not None, f"the trace names the sandbox: {ref}")
    time.sleep(6)  # let it get into the clone/install/test commands
    print("       " + kill_sandbox(ref))

    latest = wait_for_run(run_id)
    traces = assert_reproduced(run_id, latest)
    closed = events(traces, "sandbox_session_closed")
    lost = [e for e in closed if e.get("metadata", {}).get("lost")]
    check(bool(lost), f"a session was recorded as lost ({len(closed)} closed in all)")
    sessions = events(traces, "sandbox_session_created")
    check(len(sessions) >= 2, f"a fresh sandbox was opened after it ({len(sessions)} sessions)")
    failed = [e for e in events(traces, "sandbox_exec_failed") if "no longer exists" in json.dumps(e)]
    print(f"       {len(failed)} command(s) reported the loss; refs {[s.get('metadata', {}).get('sandbox_ref') for s in sessions]}")
    print("       output.md:\n" + "\n".join("         " + line for line in output_md(run_id).splitlines()[:30]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lose", action="store_true", help="kill the sandbox mid-run and expect the run to finish")
    arguments = parser.parse_args()
    part_lose() if arguments.lose else part_one()
    print("done")
