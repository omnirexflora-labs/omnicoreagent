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


# The detector is tested at the telemetry boundary: it is the one redacted by
# default, and a test at a boundary that is off would pass without testing anything.
def test_privacy_filter_redacts_common_pii_but_keeps_model_context_by_default():
    privacy = PrivacyFilter()

    assert "[REDACTED_EMAIL]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="telemetry"
    )
    assert "[REDACTED_PHONE]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="telemetry"
    )
    assert "[REDACTED_SSN]" in privacy.redact_text(SENSITIVE_TEXT, boundary="telemetry")
    assert "[REDACTED_CREDIT_CARD]" in privacy.redact_text(
        SENSITIVE_TEXT, boundary="telemetry"
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

    redacted = privacy.redact(payload, boundary="telemetry")

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
async def test_memory_keeps_the_conversation_as_written(tmp_path: Path):
    """The conversation is the agent's working state; see
    test_resume_keeps_the_call.py for what redacting it did."""
    memory = MemoryRouter("in_memory")
    agent = OmniCoreAgent(
        name="privacy-agent",
        system_instruction="Test",
        model_config={"provider": "openai", "model": "test", "api_key": "test"},
        memory_router=memory,
    )

    await agent._store_message_with_telemetry(
        "user", SENSITIVE_TEXT, metadata={"agent_name": agent.name}, session_id="session-kept"
    )
    messages = await memory.get_messages("session-kept", agent.name)

    assert "alice@example.com" in str(messages[0])


@pytest.mark.asyncio
async def test_memory_persistence_redacts_content_and_metadata_when_asked(tmp_path: Path):
    memory = MemoryRouter("in_memory")
    agent = OmniCoreAgent(
        name="privacy-agent",
        system_instruction="Test",
        model_config={"provider": "openai", "model": "test", "api_key": "test"},
        memory_router=memory,
        agent_config={"privacy_config": {"redact_memory": True}},
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


def test_workspace_writes_keep_the_agents_work_as_written(tmp_path: Path):
    """Found by the repository steward: it pushed a pyproject.toml whose
    author email had become "[REDACTED_EMAIL]" — the file had passed
    through the workspace. Files are the agent's work, not a boundary."""
    backend = WorkspaceFilesBackend(LocalWorkspaceStorage(tmp_path / "files"))
    tool = WorkspaceFilesTool(
        workspace_files_backend=backend,
        privacy_filter=PrivacyFilter(),
    )
    pyproject = 'authors = [{ name = "Alice", email = "alice@example.com" }]\n'

    tool.write("pyproject.toml", pyproject)

    stored = backend.read("pyproject.toml")
    assert "alice@example.com" in stored and "[REDACTED_EMAIL]" not in stored


def test_workspace_writes_redact_pii_when_asked(tmp_path: Path):
    backend = WorkspaceFilesBackend(LocalWorkspaceStorage(tmp_path / "files"))
    tool = WorkspaceFilesTool(
        workspace_files_backend=backend,
        privacy_filter=PrivacyFilter(PrivacyConfig(redact_workspace=True)),
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
        privacy_filter=PrivacyFilter(PrivacyConfig(redact_workspace=True)),
    )

    result = offloader.offload("search", SENSITIVE_TEXT)
    artifact = Path(result.artifact_path)

    assert artifact.read_text() != SENSITIVE_TEXT
    assert "alice@example.com" not in artifact.read_text()
    assert "alice@example.com" not in result.preview


@pytest.mark.asyncio
async def test_stream_delivery_redacts_events_before_callback_when_asked():
    delivered = []

    async def callback(event):
        delivered.append(event)

    delivery = StreamDelivery(
        callback,
        "run-privacy",
        privacy_filter=PrivacyFilter(PrivacyConfig(redact_stream=True)),
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


def test_public_result_normalization_redacts_response_when_asked():
    result = normalize_run_result(
        {"response": SENSITIVE_TEXT, "status": "success"},
        agent_name="privacy-agent",
        privacy_filter=PrivacyFilter(PrivacyConfig(redact_public=True)),
    )

    assert "alice@example.com" not in result["response"]
    assert "[REDACTED_EMAIL]" in result["response"]


def test_public_stream_errors_redact_exception_text_when_asked():
    agent = type("Agent", (), {"privacy_filter": PrivacyFilter(PrivacyConfig(redact_public=True))})()

    message = _public_error(
        agent,
        RuntimeError(f"provider rejected {SENSITIVE_TEXT}"),
    )

    assert "alice@example.com" not in message
    assert "[REDACTED_EMAIL]" in message


def test_privacy_filter_never_corrupts_generated_identifiers_or_digests():
    privacy = PrivacyFilter()
    # Generated identifiers and digests can contain a Luhn-valid digit run.
    identifier = "trace_cbe5ba4111111111111111fc7c9541fde89"
    digest = "4111111111111111" + "a" * 48
    payload = {
        "child_trace_id": identifier,
        "model_call_event_id": "event_ab4111111111111111cd",
        "context_digest": digest,
        "message_digests": [digest, "ff4111111111111111ee"],
        "reference": f"telemetry://payload/{digest}",
        "result": {"data": {"trace_id": identifier, "note": f"see {identifier}"}},
    }

    assert privacy.redact(payload, boundary="telemetry") == payload


def test_privacy_filter_still_redacts_standalone_card_numbers():
    privacy = PrivacyFilter()

    for text in (
        "4111111111111111",
        "card 4111 1111 1111 1111.",
        "card:4111-1111-1111-1111",
        "numbers (4111111111111111)",
        "order-4111111111111111",
    ):
        redacted = privacy.redact_text(text, boundary="telemetry")
        assert "[REDACTED_CREDIT_CARD]" in redacted, text
        assert "4111" not in redacted, text


def test_privacy_filter_keeps_dates_and_timestamps_intact():
    privacy = PrivacyFilter()

    for text in (
        "[CURRENT_DATETIME: 2026-09-18 12:15:12 UTC]",
        "Invoice due 2026-09-18.",
        "created_at=2026-09-18T12:15:12Z",
        "window 2026-09-18 12 to 2026-09-19 08",
    ):
        assert privacy.redact_text(text, boundary="telemetry") == text, text


def test_privacy_filter_still_redacts_phone_numbers():
    privacy = PrivacyFilter()

    for text in (
        "+1 (555) 123-4567",
        "call 555-123-4567 today",
        "07700 900123",
        "+44 20 7946 0958",
        "tel-555-123-4567",
    ):
        redacted = privacy.redact_text(text, boundary="telemetry")
        assert "[REDACTED_PHONE]" in redacted, text


def test_privacy_filter_never_alters_hyphenated_identifiers():
    import uuid

    privacy = PrivacyFilter()
    samples = [
        "chatcmpl-7fe09b14-1234-5678-9012-d995b29d39db",
        "chatcmpl-78216495-2481-4239-bc1f-088422304425",
        *(f"chatcmpl-{uuid.uuid4()}" for _ in range(3000)),
        *(f"request {uuid.uuid4()} failed" for _ in range(1000)),
    ]

    altered = [s for s in samples if privacy.redact_text(s, boundary="telemetry") != s]
    assert altered == []


def test_privacy_filter_keeps_numbers_intact():
    """Found by the trajectory acceptance: a sub-agent's usage summary carried
    ``total_time=0.0123456789``, the phone matcher took the digit run for a
    number to call, and a trace recorded at ``capture: "full"`` came back
    "redacted" — evidence corrupted by a measurement. A decimal, a
    timestamp in milliseconds or nanoseconds, a large count: these have
    the digit shape of a phone number and are not one."""
    privacy = PrivacyFilter()

    for text in (
        "total_time=0.0123456789, details={}",
        "elapsed 12345.6789012 seconds",
        "cost_usd: 0.000001234567",
        "timestamp_ms=1758440123456",
        "started_ns=1758440123456789012",
        "bytes=12345678901234",
        # A fraction whose digits pass the card checksum: seen in the suite.
        "total_time=0.4111111111111111, details={}",
        "ratio 4111111111111111.25",
    ):
        assert privacy.redact_text(text, boundary="telemetry") == text, text
        assert privacy.redact_text(text, boundary="memory") == text, text


# --- the default: records are redacted, the run's own work and output are not ---
#
# An agent that links an email to a user has to read the email, and an application
# that asked for a user's contact details has to receive them. In 0.4's first cut the
# answer run() returned and the events streamed to the application were redacted by
# default: "Contact [REDACTED_EMAIL]". The maintainer's rule (2026-09-25): redaction by
# default is for the record, not the run.


def test_by_default_only_the_record_is_redacted():
    config = PrivacyConfig()

    assert config.redact_telemetry is True
    assert config.redact_public is False
    assert config.redact_stream is False
    assert config.redact_model_io is False
    assert config.redact_memory is False
    assert config.redact_workspace is False


@pytest.mark.asyncio
async def test_the_run_sees_and_returns_real_data_and_the_trace_does_not_keep_it(tmp_path, monkeypatch):
    import json

    from omnicoreagent import OmniCoreAgent, ToolRegistry
    from test_credential_scrubbing import RecordingModel

    monkeypatch.chdir(tmp_path)
    tools = ToolRegistry()

    @tools.register_tool("find_user")
    def find_user(name: str) -> dict:
        """Find a user."""
        return {"name": name, "email": "ada@example.com"}

    agent = OmniCoreAgent(
        name="support",
        system_instruction="Hi.",
        model_config={"provider": "openai", "model": "gpt-5.6-terra", "api_key": "k"},
        local_tools=tools,
        agent_config={"guardrail_mode": "off"},
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    model = RecordingModel([("c1", "find_user", '{"name": "Ada"}')], "Ada's email is ada@example.com.")
    agent.llm_connection = model
    streamed = []

    async def on_event(event):
        streamed.append(json.dumps(getattr(event, "__dict__", event), default=str))

    try:
        result = await agent.run("What is Ada's email?", session_id="s", on_event=on_event)
        trajectory = json.dumps(await agent.get_trajectory(result["trace_id"]), default=str)
    finally:
        await agent.cleanup()

    assert "ada@example.com" in json.dumps(model.requests), "the model reads it"
    assert result["response"] == "Ada's email is ada@example.com.", "the application receives it"
    assert any("ada@example.com" in event for event in streamed), "the stream carries it"
    assert "ada@example.com" not in trajectory, "the record does not keep it"
    assert "[REDACTED_EMAIL]" in trajectory
