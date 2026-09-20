#!/usr/bin/env python3
"""P6: the page — what the steward is doing now, its runs, their spend, the approvals.

Run on the server (see scenario_p1.py for the environment):

    /opt/steward/run_scenario.sh scenario_p6.py

What it asserts, in order:
  1. the page is served by the steward's own OmniServe at /steward/ without a
     token, on the same origin as the API;
  2. the API the page reads still needs the token: without it, 401;
  3. every source the page reads answers with the token, for a real run:
     the runs listing, the run's record (its approvals and trace segments),
     its budgets, its trace, and the live event stream of a session.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from scenario_p1 import BASE, TOKEN, api, check


def raw(path: str, *, token: bool) -> tuple[int, str]:
    request = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {TOKEN}"} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def stream_head(path: str, size: int = 16_000) -> str:
    """The first bytes of a live stream (it follows the session, so it does not end)."""
    request = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read(size).decode(errors="replace")
    except (TimeoutError, OSError) as error:
        return f"(no data: {error})"


def main() -> None:
    print("P6.1 the page is public, on the API's origin")
    status, body = raw("/steward/", token=False)
    check(status == 200 and "<title>Repository steward</title>" in body, f"GET /steward/ without a token: {status}")
    check("/telemetry/events/stream" in body and "/runs/" in body, "the page reads the runtime's own API")

    print("P6.2 what the page reads still needs the token")
    for path in ("/background/runs", "/background/tasks", "/tools"):
        status, _ = raw(path, token=False)
        check(status == 401, f"{path} without a token: {status}")

    print("P6.3 every source the page reads answers, for a real run")
    listing = api("GET", "/background/runs")
    runs = listing.get("runs", listing) if isinstance(listing, dict) else listing
    check(len(runs) >= 1, f"{len(runs)} background runs to show")
    finished = next((r for r in runs if r.get("status") == "completed"), runs[0])
    run_id = finished["run_id"]
    record = api("GET", f"/runs/{run_id}")
    check(record.get("run_id") == run_id, f"record of {run_id}: status {record.get('status')}, {len(record.get('trace_ids') or [])} trace segment(s), {len(record.get('approvals') or [])} approval(s)")
    budgets = api("GET", f"/runs/{run_id}/budget").get("budgets", [])
    own = next((b for b in budgets if b["scope"] == "request" and b["meter"] == "model_cost_usd"), None)
    check(own is not None, f"its spend: ${own['spent']:.4f} of ${own['limit']}" if own else "no request budget on the record")
    trace = api("GET", f"/telemetry/runs/{run_id}/trace")
    events = (trace.get("trace") or trace).get("events", [])
    check(len(events) > 0, f"its trace: {len(events)} events")
    head = stream_head(f"/telemetry/events/stream?session_id={finished['session_id']}&run_id={run_id}")
    check("data:" in head, f"the event stream replays it: {head.count('data:')} events in the first chunk")
    print(f"       open the page: ssh -N -L 8800:127.0.0.1:8800 <server>  then  http://127.0.0.1:8800/steward/")


if __name__ == "__main__":
    main()
    print("done")
