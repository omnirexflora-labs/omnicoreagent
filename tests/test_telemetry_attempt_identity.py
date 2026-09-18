from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.background.manager import BackgroundAgentManager
from omnicoreagent.background.models import RetryPolicy
from omnicoreagent.core.runtime.deadline import run_with_timeout
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryRecorder,
    TraceFilter,
    TraceStatus,
)

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _agent(outcome, *, store=None) -> OmniCoreAgent:
    kwargs = {}
    if store is not None:
        kwargs = {"telemetry_store": store, "telemetry_recorder": TelemetryRecorder(store)}
    agent = OmniCoreAgent(
        name="attempt-agent",
        system_instruction="You are a test agent.",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off"},
        **kwargs,
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


async def _slow(**kwargs):
    await asyncio.sleep(5)
    return "too late"


@pytest.mark.asyncio
async def test_run_stopped_by_timeout_is_recorded_as_timeout():
    store = InMemoryTelemetryStore()
    agent = _agent(_slow, store=store)

    with pytest.raises(asyncio.TimeoutError):
        await run_with_timeout(agent.run("hello", session_id="session-timeout"), 0.05)

    [trace] = await store.list_traces(TraceFilter(session_id="session-timeout"))
    assert trace.status == TraceStatus.TIMEOUT
    final_state = next(e for e in trace.events if e.event_type == "final_state")
    assert final_state.output["status"] == TraceStatus.TIMEOUT.value


@pytest.mark.asyncio
async def test_run_cancelled_by_caller_is_still_recorded_as_cancelled():
    store = InMemoryTelemetryStore()
    agent = _agent(_slow, store=store)

    task = asyncio.create_task(
        run_with_timeout(agent.run("hello", session_id="session-cancel"), 5)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [trace] = await store.list_traces(TraceFilter(session_id="session-cancel"))
    assert trace.status == TraceStatus.CANCELLED


@pytest.mark.asyncio
async def test_run_with_timeout_returns_result_within_deadline():
    async def quick():
        return "ok"

    assert await run_with_timeout(quick(), 1) == "ok"
    assert await run_with_timeout(quick(), None) == "ok"


@pytest.mark.asyncio
async def test_background_attempts_carry_their_own_identity():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = _agent([RuntimeError("first attempt fails"), "done"])
    await manager.register_agent("attempt-agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="attempt-agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    queued = await manager.run_now("task")
    await manager._execute_run(queued.run_id)
    await manager._execute_run(queued.run_id)

    attempts = await manager.list_attempts(queued.run_id)
    assert len(attempts) == 2
    background_trace_id = f"trace_background_{queued.run_id}"
    agent_traces = [
        trace
        for trace in await manager.telemetry_store.list_traces()
        if trace.parent_trace_id == background_trace_id
    ]
    assert len(agent_traces) == 2
    by_attempt = {
        trace.metadata.extra["background_attempt_id"]: trace for trace in agent_traces
    }
    assert set(by_attempt) == {attempt.attempt_id for attempt in attempts}
    for attempt in attempts:
        trace = by_attempt[attempt.attempt_id]
        assert trace.metadata.extra["background_attempt_number"] == attempt.attempt_number
        assert trace.task_id == "task"
    statuses = {
        attempt.attempt_number: by_attempt[attempt.attempt_id].status
        for attempt in attempts
    }
    assert statuses == {1: TraceStatus.FAILED, 2: TraceStatus.COMPLETED}


@pytest.mark.asyncio
async def test_background_attempt_timeout_is_recorded_as_timeout():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = _agent(_slow)
    await manager.register_agent("attempt-agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="attempt-agent",
        query="do work",
        schedule={"type": "manual"},
        timeout_seconds=1,
    )

    queued = await manager.run_now("task")
    await manager._execute_run(queued.run_id)

    background_trace_id = f"trace_background_{queued.run_id}"
    [agent_trace] = [
        trace
        for trace in await manager.telemetry_store.list_traces()
        if trace.parent_trace_id == background_trace_id
    ]
    assert agent_trace.status == TraceStatus.TIMEOUT


def test_served_sync_run_timeout_is_recorded_as_timeout():
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    store = InMemoryTelemetryStore()
    agent = _agent(_slow, store=store)
    server = OmniServe(
        agent=agent,
        config=OmniServeConfig(background_enabled=False, request_timeout=1),
    )
    client = TestClient(server.app, raise_server_exceptions=False)

    response = client.post(
        "/run/sync", json={"query": "slow", "session_id": "session-served"}
    )

    assert response.status_code == 504
    traces = asyncio.run(store.list_traces(TraceFilter(session_id="session-served")))
    agent_trace = next(trace for trace in traces if trace.spans[0].kind == "agent.run")
    serve_trace = next(trace for trace in traces if trace.spans[0].kind == "serve.request")
    assert agent_trace.status == TraceStatus.TIMEOUT
    assert agent_trace.parent_trace_id == serve_trace.trace_id
    assert serve_trace.status == TraceStatus.TIMEOUT


@pytest.mark.asyncio
async def test_stop_after_marks_timeouts_and_leaves_outer_cancels_alone():
    from omnicoreagent.core.runtime.deadline import current_stop_reason, stop_after

    seen = []

    async def body():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            seen.append(current_stop_reason())
            raise

    with pytest.raises(asyncio.TimeoutError):
        async with stop_after(0.02):
            await body()

    async def outer():
        async with stop_after(5):
            await body()

    task = asyncio.create_task(outer())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == ["timeout", None]
