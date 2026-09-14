from __future__ import annotations

from pathlib import Path

import pytest

from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.agents.react_agent import ReactAgent
from omnicoreagent.core.runtime.config import AgentConfig
from omnicoreagent.core.runtime.streaming import StreamDelivery
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.workspace.files import WorkspaceFilesBackend
from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage
from omnicoreagent.core.workspace.tools import WorkspaceFilesTool
from omnicoreagent.serve.serialization import normalize_run_result
from omnicoreagent.serve.sse import _public_error


SENSITIVE_TEXT = (
    "Contact Alice at alice@example.com or +1 (555) 123-4567. "
    "SSN 123-45-6789; card 4111 1111 1111 1111."
)


def test_privacy_filter_redacts_common_pii_but_keeps_model_context_by_default():
    privacy = PrivacyFilter()

    assert "[REDACTED_EMAIL]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="public"
    )
    assert "[REDACTED_PHONE]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="public"
    )
    assert "[REDACTED_SSN]" in privacy.redact_text(SENSITIVE_TEXT, boundary="public")
    assert "[REDACTED_CREDIT_CARD]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="public"
    )
    assert privacy.redact_text(SENSITIVE_TEXT, boundary="model") == SENSITIVE_TEXT


def test_privacy_config_validates_boundaries_and_categories():
    with pytest.raises(ValueError, match="redact_public"):
        PrivacyConfig(redact_public="yes")
    with pytest.raises(ValueError, match="categories"):
        PrivacyConfig(categories=["passport"])
    with pytest.raises(ValueError, match="Unknown privacy boundary"):
        PrivacyFilter().redact(SENSITIVE_TEXT, boundary="unknown")


def test_privacy_boundaries_can_be_disabled_explicitly():
    privacy = PrivacyFilter(
        PrivacyConfig(redact_public=False, redact_model_io=True, categories=["email"])
    )

    assert privacy.redact_text(SENSITIVE_TEXT, boundary="public") == SENSITIVE_TEXT
    assert "[REDACTED_EMAIL]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="model"
    )


def test_privacy_filter_preserves_protocol_identifiers():
    privacy = PrivacyFilter()
    payload = {
        "trace_id": "trace_947643490111d",
        "run_id": "run_4111111111111111",
        "message": "alice@example.com",
    }

    redacted = privacy.redact(payload, boundary="public")

    assert redacted["trace_id"] == payload["trace_id"]
    assert redacted["run_id"] == payload["run_id"]
    assert redacted["message"] == "[REDACTED_EMAIL]"


@pytest.mark.asyncio
async def test_telemetry_redacts_pii_before_trace_storage():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-privacy",
        actor=TelemetryActor(type=ActorType.AGENT, name="privacy-agent"),
        input={"message": SENSITIVE_TEXT},
    )
    await recorder.emit_event(
        "user_message",
        actor=TelemetryActor(type=ActorType.USER),
        input={"message": SENSITIVE_TEXT},
    )
    await recorder.end_trace(output={"response": SENSITIVE_TEXT})

    trace = await store.get_trace(context.trace_id)
    assert trace is not None
    serialized = str(trace.model_dump())
    assert "alice@example.com" not in serialized
    assert "123-45-6789" not in serialized
    assert "[REDACTED_EMAIL]" in serialized


@pytest.mark.asyncio
async def test_memory_persistence_redacts_content_and_metadata(tmp_path: Path):
    memory = MemoryRouter("in_memory")
    agent = OmniCoreAgent(
        name="privacy-agent",
        system_instruction="Test",
        model_config={"provider": "openai", "model": "test", "api_key": "test"},
        memory_router=memory,
    )

    await agent._store_message_with_telemetry(
        "user",
        SENSITIVE_TEXT,
        metadata={"raw": SENSITIVE_TEXT, "agent_name": agent.name},
        session_id="session-privacy",
    )
    messages = await memory.get_messages("session-privacy", agent.name)

    assert len(messages) == 1
    assert "alice@example.com" not in str(messages[0])
    assert "[REDACTED_EMAIL]" in str(messages[0])


def test_workspace_writes_redact_pii(tmp_path: Path):
    backend = WorkspaceFilesBackend(LocalWorkspaceStorage(tmp_path / "files"))
    tool = WorkspaceFilesTool(
        workspace_files_backend=backend,
        privacy_filter=PrivacyFilter(),
    )

    tool.write("notes.txt", SENSITIVE_TEXT)
    stored = backend.read("notes.txt")

    assert "alice@example.com" not in stored
    assert "[REDACTED_EMAIL]" in stored


def test_react_runtime_wires_privacy_filter_into_workspace_offloader():
    agent = ReactAgent(
        config=AgentConfig(
            agent_name="privacy-agent",
            privacy_config={"categories": ["email"]},
        )
    )

    assert agent.tool_offloader._privacy_filter is not None
    assert agent.tool_offloader._privacy_filter.config.categories == ["email"]


def test_offloaded_workspace_artifact_redacts_full_payload_and_preview(tmp_path: Path):
    offloader = ToolResponseOffloader(
        base_dir=str(tmp_path),
        privacy_filter=PrivacyFilter(),
    )

    result = offloader.offload("search", SENSITIVE_TEXT)
    artifact = Path(result.artifact_path)

    assert artifact.read_text() != SENSITIVE_TEXT
    assert "alice@example.com" not in artifact.read_text()
    assert "alice@example.com" not in result.preview


@pytest.mark.asyncio
async def test_stream_delivery_redacts_events_before_callback():
    delivered = []

    async def callback(event):
        delivered.append(event)

    delivery = StreamDelivery(
        callback,
        "run-privacy",
        privacy_filter=PrivacyFilter(),
    )
    await delivery.emit(
        {"type": "text_delta", "text": SENSITIVE_TEXT},
        agent_name="privacy-agent",
        run_id="run-privacy",
        session_id="session-privacy",
        trace_id="trace-privacy",
    )

    assert len(delivered) == 1
    assert "alice@example.com" not in delivered[0]["text"]
    assert "[REDACTED_EMAIL]" in delivered[0]["text"]


def test_public_result_normalization_redacts_response():
    result = normalize_run_result(
        {"response": SENSITIVE_TEXT, "status": "success"},
        agent_name="privacy-agent",
        privacy_filter=PrivacyFilter(),
    )

    assert "alice@example.com" not in result["response"]
    assert "[REDACTED_EMAIL]" in result["response"]


def test_public_stream_errors_redact_exception_text():
    agent = type("Agent", (), {"privacy_filter": PrivacyFilter()})()

    message = _public_error(
        agent,
        RuntimeError(f"provider rejected {SENSITIVE_TEXT}"),
    )

    assert "alice@example.com" not in message
    assert "[REDACTED_EMAIL]" in message
