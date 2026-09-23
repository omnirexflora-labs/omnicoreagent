from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.background.manager import BackgroundAgentManager
from omnicoreagent.core.runtime import construction
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    CaptureState,
    InMemoryTelemetryStore,
    JsonlTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
    TraceFilter,
)

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def test_capture_presets_fill_only_unset_recording_fields():
    default = TelemetryConfig(capture="default")
    full = TelemetryConfig()
    full_without_prompts = TelemetryConfig(record_model_prompts=False)
    default_with_responses = TelemetryConfig(capture="default", record_model_responses=True)

    assert (default.record_model_prompts, default.record_model_responses) == (False, False)
    assert (default.record_inputs, default.record_outputs, default.record_tool_results) == (
        True,
        True,
        True,
    )
    assert (full.record_model_prompts, full.record_model_responses) == (True, True)
    assert full_without_prompts.record_model_prompts is False
    assert full_without_prompts.record_model_responses is True
    assert default_with_responses.record_model_responses is True
    assert default.fingerprint() != full.fingerprint()
    with pytest.raises(ValueError, match="capture must be one of"):
        TelemetryConfig(capture="everything")


def test_capture_preset_round_trips_through_dict_and_replace():
    from dataclasses import asdict, replace

    full = TelemetryConfig(capture="full", record_model_prompts=False)

    assert TelemetryConfig(**asdict(full)) == full
    assert replace(full, strict=True).record_model_prompts is False


@pytest.mark.asyncio
async def test_full_capture_records_model_responses():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(capture="full"))
    context = await recorder.start_trace(trace_id="trace-full")
    await recorder.emit_event("model_response", output={"content": "visible"})
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    event = trace.events[-1]
    assert event.output == {"content": "visible"}
    assert event.output_capture.state == CaptureState.AVAILABLE


def test_default_storage_is_durable_jsonl_in_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))

    store = construction.default_telemetry_store(telemetry_config=TelemetryConfig())

    assert isinstance(store, JsonlTelemetryStore)
    assert store.path == (tmp_path / "ws" / "telemetry" / "traces.jsonl").resolve()


def test_memory_storage_is_an_explicit_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))

    store = construction.default_telemetry_store(
        telemetry_config=TelemetryConfig(storage="memory")
    )

    assert isinstance(store, InMemoryTelemetryStore)


@pytest.mark.parametrize("storage", ["auto", "jsonl"])
def test_cloud_workspace_keeps_telemetry_in_a_local_file(monkeypatch, tmp_path, storage):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))

    store = construction.default_telemetry_store(
        telemetry_config=TelemetryConfig(storage=storage),
        workspace_config={"workspace_backend": "s3", "s3_bucket": "example"},
    )

    assert isinstance(store, JsonlTelemetryStore)
    assert store.path == (tmp_path / "ws" / "telemetry" / "traces.jsonl").resolve()


def test_stores_for_the_same_file_are_one_object(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))

    first = construction.default_telemetry_store(telemetry_config=TelemetryConfig())
    second = construction.default_telemetry_store(telemetry_config=TelemetryConfig())
    explicit = construction.default_telemetry_store(
        telemetry_config=TelemetryConfig(
            storage="jsonl",
            storage_path=str(tmp_path / "ws" / "telemetry" / "traces.jsonl"),
        )
    )
    other = construction.default_telemetry_store(
        telemetry_config=TelemetryConfig(
            storage="jsonl", storage_path=str(tmp_path / "other.jsonl")
        )
    )

    assert first is second is explicit
    assert other is not first


@pytest.mark.asyncio
async def test_default_agents_and_manager_share_one_durable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))
    manager = BackgroundAgentManager()
    first = OmniCoreAgent(name="first", system_instruction="x", model_config=_MODEL)
    second = OmniCoreAgent(name="second", system_instruction="x", model_config=_MODEL)
    first._ensure_telemetry()
    second._ensure_telemetry()

    await manager.register_agent("first", first)

    assert first.telemetry_store is second.telemetry_store is manager.telemetry_store
    assert isinstance(manager.telemetry_store, JsonlTelemetryStore)


@pytest.mark.asyncio
async def test_default_agent_trace_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "ws"))
    agent = OmniCoreAgent(
        name="durable",
        system_instruction="x",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off"},
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    result = await agent.run("hello", session_id="durable-session")

    # A fresh store object reads the file the way a restarted process would.
    reopened = JsonlTelemetryStore(Path(agent.telemetry_store.path))
    [trace] = await reopened.list_traces(TraceFilter(session_id="durable-session"))
    assert trace.trace_id == result["trace_id"]
    assert agent._telemetry_metadata()["telemetry_storage"] == "jsonl"


@pytest.mark.asyncio
async def test_a_trace_records_the_whole_trajectory_by_default():
    """The maintainer's decision, 2026-09-22: a trace is worth keeping only
    if it holds what the model was actually sent, so `capture: "full"` is the
    default. `capture: "default"` stays for a deployment that must not
    record prompts. Personal data is redacted from telemetry either way."""
    from omnicoreagent.core.telemetry import TelemetryConfig

    config = TelemetryConfig()

    assert config.capture == "full"
    assert config.record_model_prompts and config.record_model_responses
    privacy_first = TelemetryConfig(capture="default")
    assert not privacy_first.record_model_prompts
