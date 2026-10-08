"""A call that began and then timed out has an unknown outcome, not a timeout.

The support desk chaos run (2026-10-07): a tool in a worker thread cannot be
killed, so when ``tool_call_timeout`` fires on a call that had already begun,
the thread keeps going and its side effect can land after the model was told
the call failed. The run record said ``timeout`` and the model told a customer
"The refund was not issued". A call that never began (still queued for a
thread) and a call that is safe to repeat keep the plain timeout.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import SpanStatus
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_run_suspend import RecordingModel
from test_telemetry_tool_record import _MODEL

HOLD = 0.8  # how long the tool sits after its write, past the 0.1 s limit


def _tools(ledger: list, landed: threading.Event) -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order_id: str) -> dict:
        ledger.append(order_id)  # the effect
        time.sleep(HOLD)  # the desk's DESK_REFUND_HOLD, after the write
        landed.set()
        return {"issued": True}

    @tools.register_tool("lookup", description="Looks an order up.", idempotent=True)
    def lookup(order_id: str) -> dict:
        ledger.append(f"lookup {order_id}")
        time.sleep(HOLD)
        landed.set()
        return {"found": True}

    return tools


async def _run(tools, calls):
    model = RecordingModel(calls, "done")
    agent = OmniCoreAgent(
        name="desk", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = model
    agent.agent.tool_call_timeout = 0.1
    result = await agent.run("go", session_id="chaos")
    return agent, model, result


@pytest.mark.asyncio
async def test_a_started_non_idempotent_call_that_timed_out_is_unknown():
    ledger: list = []
    landed = threading.Event()
    agent, model, result = await _run(
        _tools(ledger, landed), [("c1", "refund", '{"order_id": "2001"}')]
    )

    # The thread was not stopped: the effect is there, and lands in full later.
    assert ledger == ["2001"]
    run = await agent.get_run(result["run_id"])
    [call] = run["tool_calls"]
    assert call["outcome"] == "unknown", call

    told = next(m for m in model.calls[-1] if m.get("tool_call_id") == "c1")
    body = json.loads(told["content"])
    assert body["error_type"] == "unknown_outcome", body
    assert "outcome is unknown" in body["message"]
    assert "before" in body["message"] and "failed" in body["message"], body["message"]

    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.status == SpanStatus.TIMEOUT
    [error] = [e for e in trace.events if e.event_type == "tool_error"]
    assert error.metadata["phase"] == "timeout"
    assert error.metadata["may_still_complete"] is True

    await asyncio.to_thread(landed.wait, 5)  # let the thread finish before cleanup
    await agent.cleanup()


@pytest.mark.asyncio
async def test_an_idempotent_call_that_timed_out_is_still_a_timeout():
    ledger: list = []
    landed = threading.Event()
    agent, model, result = await _run(
        _tools(ledger, landed), [("c1", "lookup", '{"order_id": "2001"}')]
    )

    run = await agent.get_run(result["run_id"])
    assert run["tool_calls"][0]["outcome"] == "timeout"
    told = next(m for m in model.calls[-1] if m.get("tool_call_id") == "c1")
    assert json.loads(told["content"])["error_type"] == "timeout"

    await asyncio.to_thread(landed.wait, 5)
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_call_that_never_began_is_still_a_timeout(monkeypatch):
    """The call waits for a worker thread (all are busy), times out without
    ever starting, and no effect can land."""
    ledger: list = []
    tools = ToolRegistry()

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order_id: str) -> dict:
        ledger.append(order_id)
        return {"issued": True}

    real_to_thread = asyncio.to_thread

    async def queued_forever(function, *args, **kwargs):
        # Everything else in the process still gets a thread; only the tool
        # waits for one, as a call behind a full pool does.
        if getattr(function, "__wrapped__", function) is refund:
            await asyncio.sleep(30)
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", queued_forever)
    agent, model, result = await _run(tools, [("c1", "refund", '{"order_id": "2001"}')])
    run = await agent.get_run(result["run_id"])
    assert run["tool_calls"][0]["outcome"] == "timeout"
    told = next(m for m in model.calls[-1] if m.get("tool_call_id") == "c1")
    assert json.loads(told["content"])["error_type"] == "timeout"
    assert ledger == []
    await agent.cleanup()
