from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryExporter,
    InMemoryTelemetryStore,
    LangSmithTelemetryExporter,
    OTelTraceMapper,
    OTLPHttpTelemetryExporter,
    OpikTelemetryExporter,
    SpanStatus,
    TelemetryActor,
    TelemetryConfig,
    TelemetryExportError,
    TelemetryExporter,
    TelemetryNormalizer,
    TelemetryEvent,
    TelemetryRecorder,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
    build_telemetry_exporter,
)


def _trace() -> TelemetryTrace:
    actor = TelemetryActor(type=ActorType.AGENT, name="assistant")
    root = TelemetrySpan(
        trace_id="trace-export",
        span_id="span-root",
        name="agent.run",
        kind="agent.run",
        actor=actor,
        status=SpanStatus.OK,
        attributes={"custom": {"nested": True}},
    )
    tool = TelemetrySpan(
        trace_id="trace-export",
        span_id="span-tool",
        parent_span_id="span-root",
        name="tool.call",
        kind="tool.call",
        actor=TelemetryActor(type=ActorType.TOOL, name="search"),
        status=SpanStatus.ERROR,
        error={"type": "RuntimeError", "message": "boom"},
    )
    return TelemetryTrace(
        trace_id="trace-export",
        root_span_id="span-root",
        status=TraceStatus.COMPLETED,
        run_id="run-export",
        session_id="session-export",
        spans=[root, tool],
        events=[
            TelemetryEvent(
                trace_id="trace-export",
                span_id="span-root",
                event_type="user_message",
                actor=TelemetryActor(type=ActorType.USER),
                input={"message": "hello"},
            ),
            TelemetryEvent(
                trace_id="trace-export",
                span_id="span-tool",
                event_type="tool_error",
                actor=TelemetryActor(type=ActorType.TOOL, name="search"),
                error={"type": "RuntimeError", "message": "boom"},
                metadata={"run_id": "run-export"},
            ),
        ],
    )


def test_otel_mapper_preserves_trace_span_events_and_attributes():
    records = OTelTraceMapper().map_trace(_trace())

    assert [record.span_id for record in records] == ["span-root", "span-tool"]
    assert records[1].parent_span_id == "span-root"
    assert records[0].attributes["omnicoreagent.trace_id"] == "trace-export"
    assert records[0].attributes["omnicoreagent.run_id"] == "run-export"
    assert records[0].events[0].attributes["omnicoreagent.input"] == (
        '{"message": "hello"}'
    )
    assert records[1].status == "error"
    assert records[1].events[0].name == "tool_error"
    assert records[1].events[0].attributes["error.type"] == "RuntimeError"


def test_otlp_endpoint_resolution_preserves_traces_endpoint_with_trailing_slash():
    exporter = OTLPHttpTelemetryExporter(
        endpoint="http://localhost:4318/v1/traces/",
    )

    assert exporter._resolved_endpoint() == "http://localhost:4318/v1/traces"


def test_otel_mapper_preserves_normalized_events_when_root_span_is_missing():
    trace = TelemetryTrace(
        trace_id="trace-missing-root",
        root_span_id="missing-root",
        status=TraceStatus.PARTIAL,
    )

    records = OTelTraceMapper().map_trace(TelemetryNormalizer().normalize(trace))

    assert len(records) == 1
    assert records[0].name == "telemetry.trace"
    assert [event.name for event in records[0].events] == [
        "runtime_error",
        "final_state",
    ]


@pytest.mark.asyncio
async def test_recorder_exports_completed_trace_to_configured_exporter():
    store = InMemoryTelemetryStore()
    exporter = InMemoryTelemetryExporter()
    recorder = TelemetryRecorder(store, exporters=[exporter])

    await recorder.start_trace(trace_id="trace-auto-export", run_id="run-auto")
    await recorder.emit_event("user_message", input={"message": "hello"})
    await recorder.end_trace()

    assert [trace.trace_id for trace in exporter.traces] == ["trace-auto-export"]
    assert exporter.traces[0].status == TraceStatus.COMPLETED


@pytest.mark.asyncio
async def test_recorder_strict_mode_raises_export_errors():
    class BrokenExporter(InMemoryTelemetryExporter):
        async def export_trace(self, trace):
            raise RuntimeError("export failed")

    recorder = TelemetryRecorder(
        InMemoryTelemetryStore(),
        TelemetryConfig(strict=True),
        exporters=[BrokenExporter()],
    )

    await recorder.start_trace(trace_id="trace-strict-export")
    with pytest.raises(RuntimeError, match="export failed"):
        await recorder.end_trace()


@pytest.mark.asyncio
async def test_agent_run_surfaces_strict_export_error_without_masking_context():
    class BrokenExporter(TelemetryExporter):
        name = "broken"

        async def export_trace(self, trace):
            raise TelemetryExportError("export failed")

    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=store,
        telemetry_recorder=TelemetryRecorder(
            store,
            TelemetryConfig(strict=True),
            exporters=[BrokenExporter()],
        ),
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    with pytest.raises(TelemetryExportError, match="export failed"):
        await agent.run("hello", session_id="session-export-error")

    latest = await agent.get_latest_trace("session-export-error")
    assert latest["status"] == TraceStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_agent_run_surfaces_non_telemetry_strict_export_error():
    class BrokenExporter(TelemetryExporter):
        name = "broken"

        async def export_trace(self, trace):
            raise OSError("disk full")

    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=store,
        telemetry_recorder=TelemetryRecorder(
            store,
            TelemetryConfig(strict=True),
            exporters=[BrokenExporter()],
        ),
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    with pytest.raises(OSError, match="disk full"):
        await agent.run("hello", session_id="session-export-os-error")


def test_vendor_exporter_presets_build_otlp_headers(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "agent-prod")
    monkeypatch.setenv("LANGSMITH_OTEL_ENDPOINT", "https://smith.example/otel/v1/traces")
    monkeypatch.setenv("OPIK_API_KEY", "opik-key")
    monkeypatch.setenv("OPIK_WORKSPACE", "workspace")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "project")
    monkeypatch.setenv("OPIK_OTEL_ENDPOINT", "https://opik.example/otel/v1/traces")

    langsmith = build_telemetry_exporter("langsmith")
    opik = build_telemetry_exporter("opik")

    assert isinstance(langsmith, LangSmithTelemetryExporter)
    assert langsmith.endpoint == "https://smith.example/otel/v1/traces"
    assert langsmith.headers["x-api-key"] == "ls-key"
    assert langsmith.headers["Langsmith-Project"] == "agent-prod"
    assert isinstance(opik, OpikTelemetryExporter)
    assert opik.endpoint == "https://opik.example/otel/v1/traces"
    assert opik.headers["Authorization"] == "opik-key"
    assert opik.headers["Comet-Workspace"] == "workspace"
    assert opik.headers["projectName"] == "project"


@pytest.mark.asyncio
async def test_agent_export_trace_uses_exact_trace_and_supplied_exporter():
    store = InMemoryTelemetryStore()
    exporter = InMemoryTelemetryExporter()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=store,
        telemetry_recorder=TelemetryRecorder(store),
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    result = await agent.run("hello", session_id="session-export")
    exported = await agent.export_trace(result["trace_id"], exporters=[exporter])

    assert exported == [
        {
            "exporter": "memory",
            "trace_id": result["trace_id"],
            "exported_spans": 1,
            "exported_events": 2,
            "destination": "memory",
            "metadata": {},
        }
    ]
    assert exporter.traces[0].trace_id == result["trace_id"]


@pytest.mark.asyncio
async def test_agent_export_trace_uses_exporters_from_injected_recorder():
    store = InMemoryTelemetryStore()
    exporter = InMemoryTelemetryExporter()
    agent = OmniCoreAgent(
        name="telemetry-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"},
        agent_config={"guardrail_mode": "off"},
        telemetry_store=store,
        telemetry_recorder=TelemetryRecorder(store, exporters=[exporter]),
    )
    agent._initialized = True
    agent.agent = MagicMock()
    agent.agent.run = AsyncMock(return_value="done")
    agent.mcp_client = None
    agent.llm_connection = MagicMock()
    agent.memory_router = MagicMock()
    agent.memory_router.store_message = AsyncMock()
    agent.memory_router.get_messages = AsyncMock(return_value=[])

    result = await agent.run("hello", session_id="session-recorder-exporter")
    exporter.traces.clear()
    exported = await agent.export_trace(result["trace_id"])

    assert exported[0]["exporter"] == "memory"
    assert exporter.traces[0].trace_id == result["trace_id"]
