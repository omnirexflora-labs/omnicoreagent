#!/usr/bin/env python3
"""What the framework's own work costs, measured against an instant model.

Every millisecond here is ours: the model answers immediately, tools return
immediately, and the stores are in memory. Run it before and after a change
that is meant to make something faster, and put the two outputs side by side.

    uv run python engineering/validation/perf_probe.py
    uv run python engineering/validation/perf_probe.py --rounds 40 --json

The counts matter more than the milliseconds. A machine's milliseconds are its
own, but "one request serialized 7,675 objects" is a fact about the code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
# A workspace of its own each time: a probe that reads a store left behind by
# the last probe measures the store's history, not this change.
os.environ.setdefault(
    "OMNICOREAGENT_WORKSPACE_DIR", tempfile.mkdtemp(prefix="omnicoreagent-probe-")
)

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest  # noqa: E402
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.token_usage import Usage  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "probe", "max_tokens": 1000}

GOVERNANCE = {
    "enabled": True,
    "policy": {
        "name": "probe",
        "mode": "strict",
        "rules": {
            "allow": [
                {"rule_id": "tools", "capability": "tool.local.call"},
                {"rule_id": "spawn", "capability": "subagent.spawn"},
            ]
        },
    },
}
BUDGETS = {
    **GOVERNANCE,
    "budgets": {
        "application_id": "probe",
        "request": [{"meter": "model_cost_usd", "limit": 1000}],
        "application": [{"meter": "model_cost_usd", "limit": 100000, "window": "day"}],
    },
}


class InstantModel:
    """Answers at once, with usage and a price, so cost paths are exercised."""

    llm_config = dict(MODEL)

    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = list(turns)
        self.calls = 0

    async def llm_call(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelTurn:
        self.calls += 1
        turn = self.turns.pop(0) if self.turns else ModelTurn(content="done")
        return ModelTurn(
            content=turn.content,
            tool_calls=turn.tool_calls,
            finish_reason=turn.finish_reason,
            usage=Usage(requests=1, request_tokens=120, response_tokens=60, total_tokens=180),
            response_metadata={"cost_usd": 0.001},
        )

    def estimate_cost(self, usage: Any) -> float:
        return 0.001


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 1}

    return tools


# Counting is done over a few rounds only: it is a structural fact, not a timing.
_COUNTED_ROUNDS = 3

ASKS_FOR_A_TOOL = ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),))


class Counter:
    """Counts what a request does, without timing it (timing wrappers lie)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self._undo: list[tuple[Any, str, Any]] = []

    def count(self, owner: Any, name: str, label: str) -> None:
        original = getattr(owner, name)
        counts = self.counts

        if asyncio.iscoroutinefunction(original):

            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                counts[label] = counts.get(label, 0) + 1
                return await original(*args, **kwargs)

        else:

            def wrapper(*args: Any, **kwargs: Any) -> Any:
                counts[label] = counts.get(label, 0) + 1
                return original(*args, **kwargs)

        setattr(owner, name, wrapper)
        self._undo.append((owner, name, original))

    def start(self) -> None:
        from omnicoreagent.core.telemetry import models as telemetry_models
        from omnicoreagent.core.telemetry import redaction, recorder, store

        self.count(telemetry_models, "to_plain", "serialize")
        self.count(redaction, "redact_payload", "redact_payload")
        self.count(recorder.TelemetryRecorder, "emit_event", "telemetry_event")
        self.count(recorder.TelemetryRecorder, "start_span", "telemetry_span")
        self.count(store.InMemoryTelemetryStore, "get_trace", "trace_read")
        self.count(store.InMemoryTelemetryStore, "append_event", "trace_write")

    def stop(self) -> None:
        for owner, name, original in reversed(self._undo):
            setattr(owner, name, original)
        self._undo.clear()


async def _agent(*, tools: bool = False, **config: Any) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name="probe",
        system_instruction="Be brief.",
        model_config=MODEL,
        local_tools=_tools() if tools else None,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            **config,
        },
    )
    await agent.initialize()
    return agent


async def _time(make_agent, model_turns, rounds: int, label: str) -> dict[str, Any]:
    agent = await make_agent()
    for warm in range(3):
        agent.llm_connection = InstantModel(*model_turns)
        await agent.run("warm up", session_id=f"{label}-warm-{warm}")

    # Timed with nothing wrapped: counting 7,000 calls per request would be
    # most of what the clock then measured.
    # CPU time, not wall time: a busy machine's wall clock measures the
    # machine, and this is meant to measure the code.
    times: list[float] = []
    cpu: list[float] = []
    for index in range(rounds):
        agent.llm_connection = InstantModel(*model_turns)
        started, started_cpu = time.perf_counter(), time.process_time()
        await agent.run("go", session_id=f"{label}-{index}")
        times.append((time.perf_counter() - started) * 1000)
        cpu.append((time.process_time() - started_cpu) * 1000)

    # Counted separately, on a fresh agent, so the counts are not the timings.
    counting = await make_agent()
    counter = Counter()
    counter.start()
    try:
        for index in range(_COUNTED_ROUNDS):
            counting.llm_connection = InstantModel(*model_turns)
            await counting.run("go", session_id=f"{label}-count-{index}")
    finally:
        counter.stop()
    rounds_counted = _COUNTED_ROUNDS

    return {
        "case": label,
        "cpu_ms": round(statistics.median(cpu), 1),
        "median_ms": round(statistics.median(times), 1),
        "p90_ms": round(sorted(times)[int(len(times) * 0.9) - 1], 1),
        "per_request": {
            name: round(value / rounds_counted, 1)
            for name, value in sorted(counter.counts.items())
        },
    }


async def startup() -> dict[str, Any]:
    started = time.perf_counter()
    agent = OmniCoreAgent(
        name="probe", system_instruction="Be brief.", model_config=MODEL,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    built = time.perf_counter()
    await agent.initialize()
    ready = time.perf_counter()
    return {
        "case": "startup",
        "construct_ms": round((built - started) * 1000, 1),
        "initialize_ms": round((ready - built) * 1000, 1),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--case", help="run only cases whose name contains this")
    arguments = parser.parse_args()

    cases = [
        ("no-op request", lambda: _agent(), []),
        ("one tool call", lambda: _agent(tools=True), [ASKS_FOR_A_TOOL]),
        ("governed", lambda: _agent(tools=True, governance_config=GOVERNANCE), [ASKS_FOR_A_TOOL]),
        ("governed + budgets", lambda: _agent(tools=True, governance_config=BUDGETS), [ASKS_FOR_A_TOOL]),
    ]
    results: list[dict[str, Any]] = [await startup()]
    for label, make_agent, turns in cases:
        if arguments.case and arguments.case not in label:
            continue
        results.append(await _time(make_agent, turns, arguments.rounds, label))

    if arguments.json:
        print(json.dumps(results, indent=2))
        return

    first = results[0]
    print(
        f"startup: construct {first['construct_ms']} ms, "
        f"initialize {first['initialize_ms']} ms\n"
    )
    header = f"{'case':<22}{'cpu':>8}{'median':>9}{'p90':>8}   what one request does"
    print(header)
    print("-" * len(header))
    for result in results[1:]:
        does = "  ".join(
            f"{name}={value:g}" for name, value in result["per_request"].items()
        )
        print(
            f"{result['case']:<22}{result['cpu_ms']:>7.1f}ms{result['median_ms']:>8.1f}ms"
            f"{result['p90_ms']:>7.1f}ms   {does}"
        )


if __name__ == "__main__":
    asyncio.run(main())
