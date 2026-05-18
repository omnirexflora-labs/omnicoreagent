from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryStream,
    TelemetryRecorder,
    TraceFilter,
    TraceStatus,
)


def _initialized_agent(
    *,
    store: InMemoryTelemetryStore | None = None,
    guardrail: object | None = None,
) -> OmniCoreAgent:
    telemetry_store = store or InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=telemetry_store,
        telemetry_recorder=TelemetryRecorder(telemetry_store),
    )
    agent._initialized = True
    agent.guardrail = guardrail
    agent.guardrail_mode = "full" if guardrail else "off"
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])
    return agent


@pytest.mark.asyncio
async def test_run_records_completed_telemetry_trace() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    result = await agent.run("hello", session_id="session-1")

    assert result["response"] == "done"
    assert result["session_id"] == "session-1"
    assert result["trace_id"].startswith("trace_")
    assert result["run_id"].startswith("run_")

    traces = await store.list_traces(TraceFilter(session_id="session-1"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.trace_id == result["trace_id"]
    assert trace.run_id == result["run_id"]
    assert trace.status == TraceStatus.COMPLETED
    assert trace.metadata.agent_name == "telemetry-agent"
    assert trace.metadata.model == "gpt-5.4-mini"
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "final_answer",
    ]
    assert trace.spans[0].kind == "agent.run"
    assert trace.spans[0].output == {"response": "done"}
    agent.agent.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_records_completed_trace_when_initializing_normally() -> None:
    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=store,
    )

    def attach_runtime_agent():
        agent.agent = MagicMock()
        agent.agent.run = AsyncMock(return_value="initialized")
        agent.mcp_client = None
        agent.llm_connection = MagicMock()

    agent._create_agent = MagicMock(side_effect=attach_runtime_agent)

    result = await agent.run("hello", session_id="session-init-success")

    assert result["response"] == "initialized"
    assert agent._initialized is True
    traces = await store.list_traces(TraceFilter(session_id="session-init-success"))
    assert len(traces) == 1
    assert traces[0].status == TraceStatus.COMPLETED
    assert [event.event_type for event in traces[0].events] == [
        "user_message",
        "final_answer",
    ]


@pytest.mark.asyncio
async def test_run_uses_supplied_run_id_for_telemetry() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    result = await agent.run(
        "hello",
        session_id="session-external-run",
        run_id="run_external",
    )

    assert result["run_id"] == "run_external"
    assert result["response"] == "done"

    traces = await store.list_traces(TraceFilter(session_id="session-external-run"))
    assert len(traces) == 1
    assert traces[0].run_id == "run_external"


@pytest.mark.asyncio
async def test_run_records_guardrail_block_as_aborted_trace() -> None:
    store = InMemoryTelemetryStore()
    guardrail_result = SimpleNamespace(
        is_safe=False,
        message="blocked",
        to_dict=lambda: {"is_safe": False, "message": "blocked"},
    )
    guardrail = SimpleNamespace(check=MagicMock(return_value=guardrail_result))
    agent = _initialized_agent(store=store, guardrail=guardrail)

    result = await agent.run("unsafe", session_id="session-guard")

    assert "safety concerns" in result["response"]
    assert result["guardrail_result"] == {"is_safe": False, "message": "blocked"}
    assert result["trace_id"].startswith("trace_")
    assert result["run_id"].startswith("run_")
    agent.agent.run.assert_not_called()

    traces = await store.list_traces(TraceFilter(session_id="session-guard"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == TraceStatus.ABORTED_SAFETY_GUARD
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "guardrail_check",
        "guardrail_violation",
        "final_answer",
    ]
    assert trace.events[1].output == {"is_safe": False, "message": "blocked"}
    assert trace.events[2].output == {"is_safe": False, "message": "blocked"}


@pytest.mark.asyncio
async def test_run_records_resource_guard_halt_as_aborted_trace() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)
    agent.agent.run = AsyncMock(
        return_value={
            "answer": "Usage limit error: request limit reached",
            "usage": Usage(requests=1),
            "_trace_status": TraceStatus.ABORTED_RESOURCE_GUARD.value,
        }
    )

    result = await agent.run("too much", session_id="session-resource-guard")

    assert result["response"] == "Usage limit error: request limit reached"
    assert "_trace_status" not in result
    traces = await store.list_traces(TraceFilter(session_id="session-resource-guard"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == TraceStatus.ABORTED_RESOURCE_GUARD
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "final_answer",
    ]


@pytest.mark.asyncio
async def test_run_records_failed_trace_before_reraising() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)
    agent.agent.run = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await agent.run("fail", session_id="session-fail")

    traces = await store.list_traces(TraceFilter(session_id="session-fail"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == TraceStatus.FAILED
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "runtime_error",
    ]
    assert trace.events[1].error.type == "RuntimeError"
    assert trace.spans[0].error.type == "RuntimeError"


@pytest.mark.asyncio
async def test_run_records_cancelled_trace_before_reraising() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)
    agent.agent.run = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await agent.run("cancel", session_id="session-cancel")

    traces = await store.list_traces(TraceFilter(session_id="session-cancel"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == TraceStatus.CANCELLED
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "final_state",
    ]
    assert trace.spans[0].error.type == "CancelledError"
    assert trace.spans[0].status.value == "cancelled"


@pytest.mark.asyncio
async def test_run_records_initialization_failure_before_reraising() -> None:
    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        telemetry_store=store,
    )
    agent.initialize = AsyncMock(side_effect=RuntimeError("init boom"))

    with pytest.raises(RuntimeError, match="init boom"):
        await agent.run("fail before init", session_id="session-init-fail")

    traces = await store.list_traces(TraceFilter(session_id="session-init-fail"))
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == TraceStatus.FAILED
    assert [event.event_type for event in trace.events] == [
        "user_message",
        "runtime_error",
    ]
    assert trace.events[1].error.type == "RuntimeError"
    assert trace.spans[0].error.type == "RuntimeError"


@pytest.mark.asyncio
async def test_run_ensures_default_telemetry_when_initialize_is_bypassed() -> None:
    agent = _initialized_agent()
    agent.telemetry_store = None
    agent.telemetry_recorder = None
    agent.telemetry_stream = None

    result = await agent.run("hello", session_id="session-default")

    assert result["trace_id"].startswith("trace_")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    assert trace.session_id == "session-default"


def test_ensure_telemetry_rejects_component_store_mismatch() -> None:
    first_store = InMemoryTelemetryStore()
    second_store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        telemetry_store=first_store,
        telemetry_recorder=TelemetryRecorder(second_store),
    )

    with pytest.raises(ValueError, match="telemetry_recorder.store"):
        agent._ensure_telemetry()


def test_ensure_telemetry_rejects_stream_store_mismatch() -> None:
    first_store = InMemoryTelemetryStore()
    second_store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        telemetry_store=first_store,
        telemetry_stream=TelemetryStream(second_store),
    )

    with pytest.raises(ValueError, match="telemetry_stream.store"):
        agent._ensure_telemetry()


def test_ensure_telemetry_derives_store_from_supplied_stream() -> None:
    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        telemetry_stream=TelemetryStream(store),
    )

    agent._ensure_telemetry()

    assert agent.telemetry_store is store
    assert agent.telemetry_recorder.store is store
    assert agent.telemetry_stream.store is store


@pytest.mark.asyncio
async def test_get_trace_accepts_returned_trace_id_and_session_id_keyword() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    result = await agent.run("hello", session_id="session-trace")

    telemetry_trace = await agent.get_trace(result["trace_id"])
    session_trace = await agent.get_trace(session_id="session-trace")

    assert telemetry_trace["trace_id"] == result["trace_id"]
    assert session_trace["trace_id"] == result["trace_id"]


@pytest.mark.asyncio
async def test_get_trace_missing_trace_id_does_not_fall_back_to_event_summary() -> None:
    agent = _initialized_agent()

    trace = await agent.get_trace("trace_missing")

    assert trace is None


@pytest.mark.asyncio
async def test_list_telemetry_traces_accepts_filter_kwargs() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    result = await agent.run("hello", session_id="session-filter")

    traces = await agent.list_telemetry_traces(run_id=result["run_id"])

    assert [trace["trace_id"] for trace in traces] == [result["trace_id"]]


@pytest.mark.asyncio
async def test_telemetry_stream_scopes_events_by_run_id_inside_same_session() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)
    agent.agent.run = AsyncMock(side_effect=["first", "second"])

    first = await agent.run("first", session_id="shared-session")
    second = await agent.run("second", session_id="shared-session")

    first_events = await agent.get_telemetry_events_after(
        cursor=None,
        session_id="shared-session",
        run_id=first["run_id"],
    )
    second_events = await agent.get_telemetry_events_after(
        cursor=None,
        session_id="shared-session",
        run_id=second["run_id"],
    )

    assert {event.trace_id for event in first_events} == {first["trace_id"]}
    assert {event.trace_id for event in second_events} == {second["trace_id"]}
    assert [event.event_type for event in first_events] == [
        "user_message",
        "final_answer",
    ]
    assert [event.event_type for event in second_events] == [
        "user_message",
        "final_answer",
    ]


@pytest.mark.asyncio
async def test_live_telemetry_stream_scopes_by_run_id_inside_same_session() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    first = await agent.run("first", session_id="shared-session")
    second = await agent.run("second", session_id="shared-session")
    live_cursor = await agent.get_telemetry_stream_cursor(session_id="shared-session")
    third_run_id = "run_live_third"

    live_stream = agent.stream_telemetry_after(
        cursor=live_cursor,
        session_id="shared-session",
        run_id=third_run_id,
    )
    try:
        next_event = asyncio.create_task(anext(live_stream))
        await asyncio.sleep(0)
        third = await agent.run(
            "third",
            session_id="shared-session",
            run_id=third_run_id,
        )
        event = await asyncio.wait_for(next_event, timeout=1)
    finally:
        await live_stream.aclose()

    assert event.trace_id == third["trace_id"]
    assert event.event_type == "user_message"
    assert event.input == {"message": "third"}
    assert first["trace_id"] != second["trace_id"]
    assert second["trace_id"] != third["trace_id"]


@pytest.mark.asyncio
async def test_parallel_runs_share_session_without_mixing_trace_context() -> None:
    store = InMemoryTelemetryStore()
    agent = _initialized_agent(store=store)

    async def fake_run(**kwargs):
        await asyncio.sleep(0)
        return kwargs["query"]

    agent.agent.run = AsyncMock(side_effect=fake_run)

    first, second = await asyncio.gather(
        agent.run("first", session_id="shared-session"),
        agent.run("second", session_id="shared-session"),
    )

    assert first["run_id"] != second["run_id"]
    assert first["trace_id"] != second["trace_id"]

    traces = await store.list_traces(TraceFilter(session_id="shared-session"))
    assert {trace.trace_id for trace in traces} == {
        first["trace_id"],
        second["trace_id"],
    }
    assert {trace.run_id for trace in traces} == {
        first["run_id"],
        second["run_id"],
    }
    assert all(trace.status == TraceStatus.COMPLETED for trace in traces)
    for trace in traces:
        assert [event.trace_id for event in trace.events] == [
            trace.trace_id,
            trace.trace_id,
        ]
        assert trace.spans[0].output == {"response": trace.events[-1].output["response"]}

    output_by_run_id = {
        trace.run_id: trace.spans[0].output["response"] for trace in traces
    }
    assert output_by_run_id[first["run_id"]] == "first"
    assert output_by_run_id[second["run_id"]] == "second"
