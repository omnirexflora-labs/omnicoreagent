#!/usr/bin/env python3
"""Many runs at once: what the runtime does under load, and what it keeps.

Scale plan, S1. The runtime had no load evidence: every measurement so far
was one request at a time. This drives many runs concurrently with
governance, budgets and telemetry on, and a scripted model (no provider, no
cost, so the numbers are the runtime's own), and reports what it cost and
what it kept.

    python engineering/validation/load_test.py --runs 200 --concurrency 20
    python engineering/validation/load_test.py --runs 200 --concurrency 20 --serve
    python engineering/validation/load_test.py --runs 60 --concurrency 10 --steps 6

What it checks, beside the timings:

- every run finished, and answered what it was asked;
- every model call's cost landed once on every budget, so the ledger equals
  the runs times the calls times the price;
- the process keeps no task, thread or file descriptor it did not start with;
- the traces are complete, and the telemetry the runs wrote is bounded per
  run (the archive holds the finished ones).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest  # noqa: E402
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.token_usage import Usage  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "load-test"}
# What telemetry records during the run; --capture sets it.
TELEMETRY: dict[str, Any] = {"capture": "full"}
CALL_COST = 0.001
GOVERNANCE = {
    "enabled": True,
    "policy": {
        "name": "load-test",
        "mode": "strict",
        "rules": {"allow": [{"rule_id": "tools", "capability": "tool.local.call"}]},
    },
    "budgets": {
        "application_id": "load",
        "application": [{"meter": "model_cost_usd", "limit": 1_000_000, "window": "day"}],
        "request": [{"meter": "model_cost_usd", "limit": 100}],
    },
}


class ScriptedModel:
    """Asks for a tool ``steps`` times, then answers.

    It keeps no counter of its own: how far a run has got is read from that
    run's own messages (one tool result per step taken). One model object
    therefore serves any number of concurrent runs, which is what the server
    does — a counter on the model would interleave between them.
    """

    llm_config = dict(MODEL)

    def __init__(self, steps: int) -> None:
        self.steps = steps

    async def llm_call(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelTurn:
        taken = sum(1 for message in messages or () if _role(message) == "tool")
        call = f"call_{taken + 1}_{uuid.uuid4().hex[:8]}"
        # A tool call for each step, then the answer.
        tool_calls = (
            (ToolRequest(call, "lookup", json.dumps({"key": f"k{taken + 1}"})),)
            if taken < self.steps
            else ()
        )
        return ModelTurn(
            content=None if tool_calls else "done",
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=Usage(requests=1, request_tokens=120, response_tokens=60, total_tokens=180),
            response_metadata={"cost_usd": CALL_COST, "model": "gpt-5.4-mini"},
        )

    def estimate_cost(self, usage: Any) -> float:
        return CALL_COST


def _role(message: Any) -> str | None:
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": len(key)}

    return tools


def _rss_mib() -> float:
    with open("/proc/self/status") as status:
        for line in status:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024
    return 0.0


def _open_files() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


def _shared_memory():
    """One memory store for every agent object, as a deployment has: run
    state, sessions and the budget ledger are shared, so the load contends
    for them the way it would in production."""
    from omnicoreagent import MemoryRouter

    return MemoryRouter(memory_store_type="in_memory")


async def _agent(
    workspace: Path, *, memory: Any = None, telemetry: dict[str, Any] | None = None
) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name="load",
        system_instruction="Answer briefly, using the tools.",
        model_config=MODEL,
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "full",
            "enable_workspace_files": False,
            "governance_config": GOVERNANCE,
        },
        memory_router=memory,
        telemetry_config={
            "storage_path": str(workspace / "traces.jsonl"),
            **TELEMETRY,
            **(telemetry or {}),
        },
    )
    await agent.initialize()
    return agent


async def _one_run(agent: OmniCoreAgent, number: int, steps: int) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    result = await agent.run(f"question {number}", session_id=f"load-{number}")
    return (time.perf_counter() - started) * 1000, result


async def _run_directly(workspace: Path, runs: int, concurrency: int, steps: int) -> dict[str, Any]:
    """Concurrent runs through the agent object, each with its own session.

    One agent object serves them all, as a server does: the model is chosen
    per run, so each run answers its own question.
    """
    agent = await _agent(workspace, memory=_shared_memory())
    agent.llm_connection = ScriptedModel(steps)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for number in range(runs):
        queue.put_nowait(number)
    latencies: list[float] = []
    answers: list[str] = []
    failures: list[str] = []

    async def worker() -> None:
        while True:
            try:
                number = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                latency, result = await _one_run(agent, number, steps)
            except Exception as exc:  # noqa: BLE001 - a failure is a result here.
                failures.append(f"{type(exc).__name__}: {exc}")
                continue
            latencies.append(latency)
            if result.get("status") != "success":
                failures.append(f"run {number}: {result.get('status')}")
            answers.append(str(result.get("response")))

    before = {"rss": _rss_mib(), "tasks": len(asyncio.all_tasks()), "threads": threading.active_count(), "fds": _open_files()}
    started, started_cpu = time.perf_counter(), time.process_time()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall = time.perf_counter() - started
    cpu = time.process_time() - started_cpu
    await _flush(agent)
    spent = await _ledger_total(agent)
    await asyncio.sleep(0.2)
    after = {"rss": _rss_mib(), "tasks": len(asyncio.all_tasks()), "threads": threading.active_count(), "fds": _open_files()}
    await agent.cleanup()
    return {
        "surface": "agent",
        "runs": runs,
        "concurrency": concurrency,
        "steps_per_run": steps,
        "wall_seconds": round(wall, 2),
        "runs_per_second": round(runs / wall, 1) if wall else None,
        "cpu_seconds": round(cpu, 2),
        "cpu_ms_per_run": round(cpu * 1000 / runs, 1),
        "latency_ms": _percentiles(latencies),
        "failures": failures[:5],
        "failed": len(failures),
        "answers_correct": sum(1 for answer in answers if answer == "done"),
        "budget_spent_usd": spent,
        "budget_expected_usd": round(runs * (steps + 1) * CALL_COST, 6),
        "before": before,
        # Threads grow with asyncio's shared executor (the telemetry writer
        # and the archive's file work); tasks and open files must come back.
        "after": after,
        "telemetry_bytes": _telemetry_bytes(workspace),
        "telemetry_bytes_per_run": round(sum(_telemetry_bytes(workspace).values()) / runs),
    }


async def _flush(agent: OmniCoreAgent) -> None:
    """Wait for telemetry's writer thread, so the bytes on disk are all of them."""
    flush = getattr(agent.telemetry_store, "flush", None)
    if flush is not None:
        await flush()


async def _ledger_total(agent: OmniCoreAgent) -> float:
    from omnicoreagent.core.budgets import BudgetLedger

    ledger = BudgetLedger(agent.memory_router)
    usage = await ledger.usage("application:load:" + time.strftime("%Y-%m-%d"))
    return round(float(usage.get("model_cost_usd") or 0.0), 6)


def _telemetry_bytes(workspace: Path) -> dict[str, int]:
    log = workspace / "traces.jsonl"
    archive = workspace / "traces-archive"
    archived = sum(f.stat().st_size for f in archive.rglob("*") if f.is_file()) if archive.exists() else 0
    return {
        "log": log.stat().st_size if log.exists() else 0,
        "archive": archived,
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 1),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        "p99": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))], 1),
        "max": round(ordered[-1], 1),
    }


async def _run_served(workspace: Path, runs: int, concurrency: int, steps: int) -> dict[str, Any]:
    """The same load through OmniServe's HTTP surface."""
    import httpx
    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = await _agent(workspace, memory=_shared_memory())
    agent.llm_connection = ScriptedModel(steps)
    app = OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    latencies: list[float] = []
    answers: list[str] = []
    failures: list[str] = []
    transport = httpx.ASGITransport(app=app)
    before = {"rss": _rss_mib(), "tasks": len(asyncio.all_tasks()), "threads": threading.active_count(), "fds": _open_files()}
    started, started_cpu = time.perf_counter(), time.process_time()
    async with httpx.AsyncClient(transport=transport, base_url="http://serve", timeout=120) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(number: int) -> None:
            async with semaphore:
                request_started = time.perf_counter()
                try:
                    response = await client.post(
                        "/run/sync", json={"query": f"question {number}", "session_id": f"served-{number}"}
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{type(exc).__name__}: {exc}")
                    return
                latencies.append((time.perf_counter() - request_started) * 1000)
                if response.status_code != 200:
                    failures.append(f"run {number}: {response.status_code} {response.text[:80]}")
                    return
                body = response.json()
                if body.get("status") != "success":
                    failures.append(f"run {number}: {body.get('status')}")
                answers.append(str(body.get("response")))

        await asyncio.gather(*(one(number) for number in range(runs)))
    wall = time.perf_counter() - started
    cpu = time.process_time() - started_cpu
    await _flush(agent)
    spent = await _ledger_total(agent)
    await asyncio.sleep(0.2)
    after = {"rss": _rss_mib(), "tasks": len(asyncio.all_tasks()), "threads": threading.active_count(), "fds": _open_files()}
    await agent.cleanup()
    return {
        "surface": "omniserve",
        "runs": runs,
        "concurrency": concurrency,
        "steps_per_run": steps,
        "wall_seconds": round(wall, 2),
        "runs_per_second": round(runs / wall, 1) if wall else None,
        "cpu_seconds": round(cpu, 2),
        "cpu_ms_per_run": round(cpu * 1000 / runs, 1),
        "latency_ms": _percentiles(latencies),
        "failed": len(failures),
        "failures": failures[:5],
        "answers_correct": sum(1 for answer in answers if answer == "done"),
        "budget_spent_usd": spent,
        "budget_expected_usd": round(runs * (steps + 1) * CALL_COST, 6),
        "before": before,
        "after": after,
        "telemetry_bytes": _telemetry_bytes(workspace),
        "telemetry_bytes_per_run": round(sum(_telemetry_bytes(workspace).values()) / runs),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--steps", type=int, default=2, help="tool-calling steps per run")
    parser.add_argument("--serve", action="store_true", help="drive OmniServe over HTTP instead")
    parser.add_argument(
        "--capture",
        default="full",
        choices=["full", "default"],
        help="what telemetry records (full is the runtime's default)",
    )
    parser.add_argument(
        "--storage",
        default="jsonl",
        choices=["jsonl", "memory"],
        help="where telemetry keeps it: the durable log, or memory only",
    )
    parser.add_argument("--workspace", default=None, help="where traces go (a temporary directory by default)")
    arguments = parser.parse_args()

    import tempfile

    with tempfile.TemporaryDirectory(prefix="omni-load-") as temporary:
        workspace = Path(arguments.workspace or temporary)
        peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
        global TELEMETRY
        TELEMETRY = {"capture": arguments.capture, "storage": arguments.storage}
        report = await (
            _run_served(workspace, arguments.runs, arguments.concurrency, arguments.steps)
            if arguments.serve
            else _run_directly(workspace, arguments.runs, arguments.concurrency, arguments.steps)
        )
        report["capture"] = arguments.capture
        report["storage"] = arguments.storage
        report["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
        report["peak_rss_before_mib"] = peak_before
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
