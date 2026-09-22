from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omnicoreagent.core.agents.subagent_runner import SubAgentCallRunner
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.subagents import SubagentFactory
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryRecorder,
    TraceStatus,
)

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _child(recorder: TelemetryRecorder, name: str, *, outcome) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name=name,
        system_instruction="You are a child agent.",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off"},
        telemetry_store=recorder.store,
        telemetry_recorder=recorder,
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(side_effect=outcome)
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])
    return agent


async def _parent(store):
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-parent", run_id="run-parent", session_id="session-parent"
    )
    return recorder, context


async def _delegation(store, kind="subagent.run"):
    parent = await store.get_trace("trace-parent")
    return next(span for span in parent.spans if span.kind == kind)


async def _child_trace(store):
    return next(
        trace
        for trace in await store.list_traces()
        if trace.parent_trace_id == "trace-parent"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "terminal_event"),
    [
        (lambda **kwargs: "child done", "subagent_result"),
        (RuntimeError("child failed"), "subagent_error"),
    ],
)
async def test_configured_delegation_records_child_ids_on_success_and_error(
    outcome, terminal_event
):
    store = InMemoryTelemetryStore()
    recorder, _ = await _parent(store)
    child = _child(recorder, "configured-child", outcome=outcome)

    await SubAgentCallRunner("parent").run(
        {"agent": "configured-child", "parameters": {"query": "child task"}},
        [child],
        "session-parent",
        telemetry_recorder=recorder,
    )

    child_trace = await _child_trace(store)
    delegation = await _delegation(store)
    assert child_trace.parent_span_id == delegation.span_id
    parent_events = (await store.get_trace("trace-parent")).events
    spawn = next(e for e in parent_events if e.event_type == "subagent_spawn")
    assert spawn.metadata["child_run_id"] == child_trace.run_id
    assert delegation.output["child_trace_id"] == child_trace.trace_id
    assert delegation.output["child_run_id"] == child_trace.run_id
    parent = await store.get_trace("trace-parent")
    terminal = next(e for e in parent.events if e.event_type == terminal_event)
    assert terminal.metadata["child_trace_id"] == child_trace.trace_id
    assert terminal.metadata["child_run_id"] == child_trace.run_id


@pytest.mark.asyncio
async def test_configured_delegation_records_child_ids_on_cancellation():
    store = InMemoryTelemetryStore()
    recorder, _ = await _parent(store)
    child = _child(recorder, "configured-child", outcome=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await SubAgentCallRunner("parent").run(
            {"agent": "configured-child", "parameters": {"query": "child task"}},
            [child],
            "session-parent",
            telemetry_recorder=recorder,
        )

    child_trace = await _child_trace(store)
    assert child_trace.status == TraceStatus.CANCELLED
    delegation = await _delegation(store)
    assert delegation.output["child_trace_id"] == child_trace.trace_id
    assert delegation.output["child_run_id"] == child_trace.run_id


def _factory(recorder: TelemetryRecorder, child: OmniCoreAgent) -> SubagentFactory:
    factory = SubagentFactory(base_model_config=_MODEL, telemetry_recorder=recorder)
    factory.create_subagent = MagicMock(return_value=child)
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("output_error", [None, "The requested output was not created."])
async def test_dynamic_spawn_records_delegation_span_and_output_verification(
    output_error,
):
    store = InMemoryTelemetryStore()
    recorder, _ = await _parent(store)
    child = _child(recorder, "subagent_writer", outcome=lambda **kwargs: "written")
    factory = _factory(recorder, child)

    with patch.object(factory, "_workspace_output_error", return_value=output_error):
        result = await factory.run_subagent(
            name="writer",
            role="Writer",
            task="Write the report",
            output_path="tasks/report.md",
        )

    child_trace = await _child_trace(store)
    delegation = await _delegation(store)
    assert child_trace.parent_span_id == delegation.span_id
    assert result["data"]["trace_id"] == child_trace.trace_id
    assert result["data"]["run_id"] == child_trace.run_id
    assert delegation.output["child_trace_id"] == child_trace.trace_id
    assert delegation.output["child_run_id"] == child_trace.run_id
    assert delegation.output["workspace_output"] == {
        "path": "tasks/report.md",
        "verified": output_error is None,
        "error": output_error,
    }
    assert delegation.output["status"] == ("success" if output_error is None else "error")


@pytest.mark.asyncio
async def test_dynamic_spawn_exception_keeps_child_trace_identity():
    store = InMemoryTelemetryStore()
    recorder, _ = await _parent(store)
    child = _child(recorder, "subagent_writer", outcome=RuntimeError("child crashed"))
    factory = _factory(recorder, child)

    result = await factory.run_subagent(
        name="writer",
        role="Writer",
        task="Write the report",
        output_path="tasks/report.md",
    )

    child_trace = await _child_trace(store)
    assert child_trace.status == TraceStatus.FAILED
    assert result["status"] == "error"
    assert result["data"]["trace_id"] == child_trace.trace_id
    assert result["data"]["run_id"] == child_trace.run_id
    delegation = await _delegation(store)
    assert delegation.output["child_trace_id"] == child_trace.trace_id
    assert delegation.output["status"] == "error"
