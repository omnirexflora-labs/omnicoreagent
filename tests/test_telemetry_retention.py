from __future__ import annotations

import asyncio
from datetime import timedelta
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.core.runtime import construction
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    JsonlTelemetryStore,
    TelemetryActor,
    TelemetryConfig,
    TelemetryRecorder,
    TelemetrySpan,
    TelemetryStreamScope,
    TelemetryTrace,
    TraceStatus,
)
from omnicoreagent.core.telemetry.models import utc_now
from omnicoreagent.core.telemetry.payloads import (
    LocalTelemetryPayloadStore,
    payload_references,
)

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}
_DAY = 24 * 60 * 60


def _trace(trace_id: str, *, ended_days_ago: float | None = None) -> TelemetryTrace:
    root = TelemetrySpan(
        trace_id=trace_id,
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT),
    )
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=root.span_id,
        status=TraceStatus.RUNNING,
        spans=[root],
    )
    if ended_days_ago is not None:
        trace.status = TraceStatus.COMPLETED
        trace.started_at = utc_now() - timedelta(days=ended_days_ago, minutes=1)
        trace.ended_at = utc_now() - timedelta(days=ended_days_ago)
    return trace


def _age_file(path, days: float) -> None:
    stamp = time.time() - days * _DAY
    os.utime(path, (stamp, stamp))


def test_payload_retention_is_configured_independently(tmp_path):
    config = TelemetryConfig(
        storage="jsonl",
        storage_path=str(tmp_path / "traces.jsonl"),
        retention_days=30,
        payload_retention_days=2,
        offload_large_payloads=True,
    )

    payload_store = construction.default_telemetry_payload_store(telemetry_config=config)
    trace_store = construction.default_telemetry_store(telemetry_config=config)

    assert payload_store.retention_days == 2
    assert trace_store.retention_days == 30
    with pytest.raises(ValueError, match="payload_retention_days"):
        TelemetryConfig(payload_retention_days=-1)
    with pytest.raises(ValueError, match="memory_max_traces"):
        TelemetryConfig(memory_max_traces=0)


@pytest.mark.asyncio
async def test_payload_references_cover_descriptors_and_offloaded_stubs(tmp_path):
    store = InMemoryTelemetryStore()
    payloads = LocalTelemetryPayloadStore(tmp_path / "payloads")
    recorder = TelemetryRecorder(
        store,
        TelemetryConfig(max_payload_bytes=100, offload_large_payloads=True),
        payload_store=payloads,
    )
    context = await recorder.start_trace(trace_id="trace-refs")
    await recorder.emit_event(
        "tool_result",
        output={"content": "x" * 500},
        metadata={"detail": "y" * 500},
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    references = payload_references(trace)

    assert len(references) == 2
    assert all(ref.startswith("telemetry://payload/") for ref in references)


async def _offloading_agent(tmp_path, **config):
    agent = OmniCoreAgent(
        name="retention-agent",
        system_instruction="You are a test agent.",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off"},
        telemetry_config=TelemetryConfig(
            storage="jsonl",
            storage_path=str(tmp_path / "traces.jsonl"),
            max_payload_bytes=100,
            offload_large_payloads=True,
            **config,
        ),
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="x" * 500)
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])
    agent._ensure_telemetry()
    return agent


@pytest.mark.asyncio
async def test_prune_keeps_payloads_referenced_by_kept_traces(tmp_path):
    agent = await _offloading_agent(tmp_path, retention_days=7, payload_retention_days=7)
    result = await agent.run("hello", session_id="kept-session")
    kept = await agent.telemetry_store.get_trace(result["trace_id"])
    kept_refs = payload_references(kept)
    assert kept_refs

    payload_dir = tmp_path / "traces.jsonl.payloads"
    orphan = agent.telemetry_payload_store.write(
        {"orphan": True}, checksum="a" * 64
    )
    expired = _trace("trace-expired", ended_days_ago=30)
    await agent.telemetry_store.upsert_trace(expired)
    for path in payload_dir.iterdir():
        _age_file(path, 30)

    summary = await agent.prune_telemetry()

    assert summary["traces_removed"] == 1
    assert summary["payloads_removed"] == 1
    assert summary["payloads_retained_by_reference"] == len(kept_refs)
    remaining = {path.name.removesuffix(".json") for path in payload_dir.iterdir()}
    assert {ref.rsplit("/", 1)[1] for ref in kept_refs} <= remaining
    assert orphan.rsplit("/", 1)[1] not in remaining
    assert await agent.telemetry_store.get_trace("trace-expired") is None
    assert await agent.telemetry_store.get_trace(result["trace_id"]) is not None


@pytest.mark.asyncio
async def test_retention_runs_automatically_once_and_is_observable(tmp_path):
    path = tmp_path / "traces.jsonl"
    seed = JsonlTelemetryStore(path)
    await seed.upsert_trace(_trace("trace-expired", ended_days_ago=30))

    agent = await _offloading_agent(tmp_path, retention_days=7)
    await agent.run("first", session_id="auto-session")
    await agent.run("second", session_id="auto-session")

    status = agent.telemetry_retention_status()
    assert status["automatic_runs"] == 1
    assert status["trace_store"]["retention_days"] == 7
    assert status["trace_store"]["removed_total"] == 1
    assert status["last_cleanup"]["trigger"] == "automatic"
    assert status["payload_store"]["retention_days"] == 7
    assert status["payload_store"]["last_prune"] is not None
    assert await agent.telemetry_store.get_trace("trace-expired") is None


@pytest.mark.asyncio
async def test_in_memory_store_evicts_oldest_finished_traces_only():
    store = InMemoryTelemetryStore(max_traces=2)
    stream = store.stream_after(TelemetryStreamScope(trace_id="trace-running"), None)
    next_event = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)

    await store.upsert_trace(_trace("trace-running"))
    await store.upsert_trace(_trace("trace-old", ended_days_ago=3))
    await store.upsert_trace(_trace("trace-mid", ended_days_ago=2))
    await store.upsert_trace(_trace("trace-new", ended_days_ago=1))

    remaining = {trace.trace_id for trace in await store.list_traces()}
    assert remaining == {"trace-running", "trace-new"}
    assert store.retention_status() == {"max_traces": 2, "evicted": 2}

    from omnicoreagent.core.telemetry import TelemetryEvent

    live = TelemetryEvent(
        trace_id="trace-running",
        event_type="agent_step",
        actor=TelemetryActor(type=ActorType.AGENT),
    )
    await store.append_event("trace-running", live)
    received = await asyncio.wait_for(next_event, timeout=1)
    await stream.aclose()
    assert received.event_id == live.event_id


def test_default_memory_store_is_bounded():
    store = construction.default_telemetry_store(
        telemetry_config=TelemetryConfig(storage="memory", memory_max_traces=50)
    )

    assert store.retention_status()["max_traces"] == 50


@pytest.mark.asyncio
async def test_background_event_log_forgets_finished_traces():
    from omnicoreagent.background.manager import BackgroundAgentManager

    manager = BackgroundAgentManager()
    event_log = manager._event_log
    event = {"run_id": "run-done", "session_id": "s", "agent_id": "a"}

    await event_log.append_telemetry_event("background_run_queued", event)
    await event_log.append_telemetry_event(
        "background_run_completed", {**event, "status": "completed"}
    )

    assert event_log._telemetry_traces == set()
    trace = await manager.telemetry_store.get_trace(
        event_log.telemetry_trace_id("run-done")
    )
    assert trace.status == TraceStatus.COMPLETED


def test_served_retention_status_is_observable(tmp_path):
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = asyncio.run(_offloading_agent(tmp_path, retention_days=7))
    server = OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False))
    client = TestClient(server.app)

    response = client.get("/telemetry/retention")

    assert response.status_code == 200
    body = response.json()
    assert body["trace_store"]["retention_days"] == 7
    assert body["payload_store"]["retention_days"] == 7
    assert body["automatic_runs"] == 0
