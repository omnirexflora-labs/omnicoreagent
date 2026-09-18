from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.background.manager import BackgroundAgentManager
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
)

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _agent(**kwargs) -> OmniCoreAgent:
    return OmniCoreAgent(
        name=kwargs.pop("name", "lineage-agent"),
        system_instruction="You are a test agent.",
        model_config=_MODEL,
        agent_config=kwargs.pop("agent_config", {"guardrail_mode": "off"}),
        **kwargs,
    )


def _governed_config() -> dict:
    return {
        "guardrail_mode": "off",
        "enable_subagents": True,
        "governance_config": {
            "enabled": True,
            "profile": "strict-production",
            "sandbox_config": {"provider": "local_test"},
            "allow_test_sandbox_runtime": True,
            "policy": {
                "name": "lineage-policy",
                "mode": "strict",
                "rules": {
                    "allow": [
                        {
                            "rule_id": "allow_sandboxed_process",
                            "capability": "process.exec",
                            "constraints": {"sandbox_required": True},
                        }
                    ]
                },
            },
        },
    }


def _captured_recorders(agent: OmniCoreAgent) -> dict[str, object]:
    engine = agent.agent.governance_engine
    return {
        "governance": engine.telemetry_recorder,
        "sandbox": engine.sandbox_runtime.telemetry_recorder,
        "subagent_factory": agent._subagent_factory.telemetry_recorder,
    }


@pytest.mark.asyncio
async def test_register_agent_shares_manager_store_and_keeps_recording_policy():
    manager = BackgroundAgentManager()
    config = TelemetryConfig(record_model_responses=True, max_payload_bytes=1234)
    agent = _agent(telemetry_config=config)

    await manager.register_agent("lineage", agent)

    assert agent.telemetry_store is manager.telemetry_store
    assert agent.telemetry_recorder.store is manager.telemetry_store
    assert agent.telemetry_stream.store is manager.telemetry_store
    assert agent.telemetry_recorder.config == config


@pytest.mark.asyncio
async def test_register_agent_rejects_explicit_different_store():
    manager = BackgroundAgentManager()
    agent = _agent(telemetry_store=InMemoryTelemetryStore())

    with pytest.raises(ValueError, match="telemetry store"):
        await manager.register_agent("explicit", agent)


@pytest.mark.asyncio
async def test_register_agent_accepts_the_manager_store_explicitly():
    store = InMemoryTelemetryStore()
    manager = BackgroundAgentManager(telemetry_store=store)
    agent = _agent(telemetry_store=store)

    await manager.register_agent("same", agent)

    assert agent.telemetry_store is store


@pytest.mark.asyncio
async def test_registering_initialized_agent_rebinds_build_time_recorders():
    manager = BackgroundAgentManager()
    agent = _agent(agent_config=_governed_config())
    await agent.initialize()

    await manager.register_agent("governed", agent)

    for component, recorder in _captured_recorders(agent).items():
        assert recorder is agent.telemetry_recorder, component
        assert recorder.store is manager.telemetry_store, component


@pytest.mark.asyncio
async def test_inherited_telemetry_rebinds_build_time_recorders():
    child = _agent(name="configured-child", agent_config=_governed_config())
    await child.initialize()
    parent_recorder = TelemetryRecorder(InMemoryTelemetryStore())

    child._inherit_telemetry(parent_recorder)

    for component, recorder in _captured_recorders(child).items():
        assert recorder is parent_recorder, component


@pytest.mark.asyncio
async def test_background_trace_is_created_after_a_failed_first_upsert():
    class FlakyUpsertStore(InMemoryTelemetryStore):
        failures = 1

        async def upsert_trace(self, trace):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("store unavailable")
            return await super().upsert_trace(trace)

    store = FlakyUpsertStore()
    manager = BackgroundAgentManager(telemetry_store=store)
    event_log = manager._event_log
    event = {"run_id": "run-flaky", "session_id": "s", "agent_id": "a"}

    await event_log.append_telemetry_event("background_run_queued", event)
    await event_log.append_telemetry_event(
        "background_run_started", {**event, "status": "running"}
    )

    trace = await store.get_trace(event_log.telemetry_trace_id("run-flaky"))
    assert trace is not None
    assert [e.event_type for e in trace.events] == ["background_run_started"]
    assert trace.incomplete is True


@pytest.mark.asyncio
async def test_background_trace_creation_waits_for_a_slow_first_upsert():
    class SlowUpsertStore(InMemoryTelemetryStore):
        async def upsert_trace(self, trace):
            await asyncio.sleep(0.05)
            return await super().upsert_trace(trace)

    store = SlowUpsertStore()
    manager = BackgroundAgentManager(telemetry_store=store)
    event_log = manager._event_log
    event_log.append_timeout_seconds = 0.01
    event = {"run_id": "run-slow", "session_id": "s", "agent_id": "a"}

    await event_log.append_telemetry_event("background_run_queued", event)
    event_log.append_timeout_seconds = 2
    await event_log.append_telemetry_event(
        "background_run_started", {**event, "status": "running"}
    )

    trace = await store.get_trace(event_log.telemetry_trace_id("run-slow"))
    assert "background_run_started" in [e.event_type for e in trace.events]


@pytest.mark.asyncio
async def test_default_background_run_family_is_complete_from_the_agent():
    from unittest.mock import AsyncMock, MagicMock

    manager = BackgroundAgentManager(task_store="in_memory")
    agent = _agent(name="background-agent")
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    await manager.register_agent("background-agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="background-agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)

    background_trace_id = f"trace_background_{run.run_id}"
    family = await agent.get_trace_family(background_trace_id)
    family_ids = {trace["trace_id"] for trace in family}
    agent_traces = [
        trace
        for trace in family
        if trace["trace_id"] != background_trace_id
    ]
    assert background_trace_id in family_ids
    assert len(agent_traces) == 1
    assert agent_traces[0]["parent_trace_id"] == background_trace_id
