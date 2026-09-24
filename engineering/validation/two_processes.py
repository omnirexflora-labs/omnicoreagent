#!/usr/bin/env python3
"""Two server processes on one deployment: what S2 and S3 were for.

Scale plan, S4. Two OmniServe processes share one PostgreSQL database — the
background task store and the telemetry archive's index — and one directory of
trace bodies. Queued runs are divided between them, each run runs exactly once,
and either process can answer for a trace the other recorded.

Run each process with ``serve``, then check them with ``drive``:

    OMNICOREAGENT_SERVE_AUTH_TOKEN=... DATABASE_URL=postgresql://... \
        python engineering/validation/two_processes.py serve --port 8001
    OMNICOREAGENT_SERVE_AUTH_TOKEN=... DATABASE_URL=postgresql://... \
        python engineering/validation/two_processes.py serve --port 8002
    python engineering/validation/two_processes.py drive \
        --first http://127.0.0.1:8001 --second http://127.0.0.1:8002 --runs 8

The model is scripted, so no provider is called and the numbers and outcomes
belong to the runtime. ``drive`` prints a JSON report and exits non-zero if any
of its claims fail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "two-processes"}
TASK_ID = "s4-shared-work"
AGENT_ID = "s4"


# --- the process ----------------------------------------------------------


class ScriptedModel:
    """Answers at once. How far a run has got is read from its own messages."""

    llm_config = dict(MODEL)

    async def llm_call(self, messages: Any, tools: Any = None, **kwargs: Any):
        from omnicoreagent.core.model_protocol import ModelTurn
        from omnicoreagent.core.token_usage import Usage

        return ModelTurn(
            content="done",
            finish_reason="stop",
            usage=Usage(requests=1, request_tokens=80, response_tokens=20, total_tokens=100),
            response_metadata={"cost_usd": 0.0, "model": MODEL["model"]},
        )

    def estimate_cost(self, usage: Any) -> float:
        return 0.0


def _serve(port: int, database_url: str, bodies: str, workspace: str) -> None:
    from omnicoreagent import OmniCoreAgent
    from omnicoreagent.serve import OmniServe, OmniServeConfig

    async def build():
        agent = OmniCoreAgent(
            name=AGENT_ID,
            system_instruction="Answer briefly.",
            model_config=MODEL,
            agent_config={"enable_workspace_files": False},
            telemetry_config={
                "storage": "jsonl",
                # Each process keeps its own running traces, and shares the
                # archive of finished ones.
                "storage_path": f"{workspace}/telemetry/traces.jsonl",
                "archive_index_url": database_url,
                "archive_bodies_path": bodies,
                "retention_days": None,
            },
        )
        await agent.initialize()
        agent.llm_connection = ScriptedModel()
        return agent

    agent = asyncio.run(build())
    # Say what this process is actually keeping traces in, so a deployment
    # that meant to share an archive can see at a glance whether it does.
    store = getattr(agent, "telemetry_store", None)
    archive = getattr(store, "archive", None)
    index = getattr(archive, "index", None) if archive is not None else None
    print(
        f"telemetry: {type(store).__name__} archive: {type(archive).__name__} "
        f"index: {type(index).__name__} shared: {getattr(index, 'shared', None)}",
        flush=True,
    )
    serve = OmniServe(
        agent=agent,
        config=OmniServeConfig(
            host="0.0.0.0",
            port=port,
            background_enabled=True,
            background_agent_id=AGENT_ID,
            background_task_store="sql",
            background_task_store_url=database_url,
        ),
    )
    serve.start()


# --- the driver -----------------------------------------------------------


def _call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None):
    request = urllib.request.Request(
        f"{base}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {base}{path} -> {error.code} {error.read()[:300]!r}")


def _items(payload: Any, key: str) -> list[dict]:
    """The list in a response, whether it is the body or inside an envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        found = payload.get(key)
        if isinstance(found, list):
            return found
    return []


def _ensure_task(base: str, token: str | None) -> None:
    tasks = _call(base, "GET", "/background/tasks", token=token)
    existing = {task["task_id"] for task in _items(tasks, "tasks")}
    if TASK_ID in existing:
        return
    _call(
        base,
        "POST",
        "/background/tasks",
        {
            "task_id": TASK_ID,
            "agent_id": AGENT_ID,
            "query": "shared work",
            "schedule": {"type": "manual"},
            "overlap_policy": "allow_parallel",
        },
        token=token,
    )


def _drive(first: str, second: str, runs: int, token: str | None) -> dict[str, Any]:
    _ensure_task(first, token)
    queued = [
        _call(first, "POST", f"/background/tasks/{TASK_ID}/run", {}, token=token)
        for _ in range(runs)
    ]
    run_ids = [item.get("run_id") or item["run"]["run_id"] for item in queued]

    deadline = time.time() + 300
    finished: dict[str, dict] = {}
    while time.time() < deadline and len(finished) < len(run_ids):
        for run_id in run_ids:
            if run_id in finished:
                continue
            record = _call(first, "GET", f"/background/runs/{run_id}", token=token)
            if record.get("status") in {
                "completed",
                "failed",
                "cancelled",
                "timeout",
                "skipped",
            }:
                finished[run_id] = record
        if len(finished) < len(run_ids):
            time.sleep(0.5)

    workers: dict[str, list[str]] = {}
    attempts: dict[str, int] = {}
    for run_id in run_ids:
        listed = _call(first, "GET", f"/background/runs/{run_id}/attempts", token=token)
        items = _items(listed, "attempts")
        attempts[run_id] = len(items)
        for attempt in items:
            workers.setdefault(attempt.get("worker_id", "?"), []).append(run_id)

    # The point of the shared archive: each process answers for every trace.
    served = {"first": 0, "second": 0}
    for run_id in run_ids:
        for name, base in (("first", first), ("second", second)):
            trace = _call(base, "GET", f"/telemetry/runs/{run_id}/trace", token=token)
            body = trace.get("trace", trace)
            if body.get("events"):
                served[name] += 1

    statuses = sorted({record.get("status") for record in finished.values()})
    report = {
        "runs": len(run_ids),
        "finished": len(finished),
        "statuses": statuses,
        "attempts_per_run": sorted(set(attempts.values())),
        "workers": {worker: len(ids) for worker, ids in sorted(workers.items())},
        "traces_served_by_first": served["first"],
        "traces_served_by_second": served["second"],
    }
    failures = []
    if len(finished) != len(run_ids):
        failures.append("some runs never finished")
    if statuses != ["completed"]:
        failures.append(f"not every run completed: {statuses}")
    if set(attempts.values()) != {1}:
        failures.append(f"a run ran more than once: {attempts}")
    if len(workers) < 2:
        failures.append(f"only one process took work: {list(workers)}")
    if served["first"] != len(run_ids) or served["second"] != len(run_ids):
        failures.append("a process could not read the other's traces")
    report["failures"] = failures
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)

    serve = modes.add_parser("serve", help="run one of the two processes")
    serve.add_argument("--port", type=int, default=8001)
    serve.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    serve.add_argument("--bodies", default="/shared/telemetry-bodies")
    serve.add_argument("--workspace", default=f"/tmp/s4-{uuid4().hex[:8]}")

    drive = modes.add_parser("drive", help="queue runs and check both processes")
    drive.add_argument("--first", default="http://127.0.0.1:8001")
    drive.add_argument("--second", default="http://127.0.0.1:8002")
    drive.add_argument("--runs", type=int, default=8)
    drive.add_argument("--token", default=os.environ.get("OMNICOREAGENT_SERVE_AUTH_TOKEN"))

    arguments = parser.parse_args()
    if arguments.mode == "serve":
        if not arguments.database_url:
            raise SystemExit("serve needs --database-url or DATABASE_URL")
        _serve(arguments.port, arguments.database_url, arguments.bodies, arguments.workspace)
        return

    report = _drive(arguments.first, arguments.second, arguments.runs, arguments.token)
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if report["failures"] else 0)


if __name__ == "__main__":
    main()
