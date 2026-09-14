from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    CaptureState,
    EvidenceValidationError,
    GenericTraceEvidenceAdapter,
    OmniCoreEvidenceAdapter,
    SpanStatus,
    TelemetryActor,
    TelemetryCapture,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
    validate_portable_evidence_document,
)


def test_omnicore_adapter_preserves_cross_trace_parent_and_final_output():
    trace = TelemetryTrace(
        trace_id="trace-child",
        root_span_id="span-child",
        parent_trace_id="trace-parent",
        parent_span_id="span-parent",
        status=TraceStatus.COMPLETED,
        spans=[
            TelemetrySpan(
                trace_id="trace-child",
                span_id="span-child",
                parent_span_id="span-parent",
                name="agent.run",
                kind="agent.run",
                actor=TelemetryActor(type=ActorType.AGENT),
                status=SpanStatus.OK,
            )
        ],
        events=[
            TelemetryEvent(
                trace_id="trace-child",
                span_id="span-child",
                event_id="event-final",
                event_type="final_answer",
                actor=TelemetryActor(type=ActorType.AGENT),
                output={"response": "done"},
            )
        ],
    )

    evidence = OmniCoreEvidenceAdapter().import_trace(trace)

    assert evidence.execution_id == "trace-child"
    assert evidence.source == "production"
    assert [ref.identifier for ref in evidence.final_output_references] == [
        "event-final"
    ]
    assert not any(item.get("type") == "parent_span" for item in evidence.missing_evidence)


def test_omnicore_adapter_exposes_capture_policy_gaps_without_judging_them():
    trace = TelemetryTrace(
        trace_id="trace-private",
        root_span_id="span-private",
        status=TraceStatus.COMPLETED,
        spans=[
            TelemetrySpan(
                trace_id="trace-private",
                span_id="span-private",
                name="agent.run",
                kind="agent.run",
                actor=TelemetryActor(type=ActorType.AGENT),
                output_capture=TelemetryCapture(
                    state=CaptureState.NOT_RECORDED,
                    source="provider",
                    role="model_response",
                    reason="disabled by policy",
                ),
            )
        ],
        events=[
            TelemetryEvent(
                trace_id="trace-private",
                span_id="span-private",
                event_type="final_answer",
                actor=TelemetryActor(type=ActorType.AGENT),
                output={"response": "done"},
            )
        ],
    )

    evidence = OmniCoreEvidenceAdapter().import_trace(trace)

    assert evidence.trace["evidence_status"] == "partial"
    assert any(item["state"] == "not_recorded" for item in evidence.missing_evidence)
    assert not hasattr(evidence, "score")
    assert evidence.internal_trace is not None


def test_portable_document_is_json_contract_and_can_be_reimported():
    trace = TelemetryTrace(
        trace_id="trace-portable",
        root_span_id="span-portable",
        status=TraceStatus.COMPLETED,
        spans=[
            TelemetrySpan(
                trace_id="trace-portable",
                span_id="span-portable",
                name="agent.run",
                kind="agent.run",
                actor=TelemetryActor(type=ActorType.AGENT),
                status=SpanStatus.OK,
            )
        ],
        events=[
            TelemetryEvent(
                trace_id="trace-portable",
                span_id="span-portable",
                event_id="event-portable-final",
                event_type="final_answer",
                actor=TelemetryActor(type=ActorType.AGENT),
                output={"response": "done"},
            )
        ],
    )

    evidence = OmniCoreEvidenceAdapter().import_trace(trace)
    document = evidence.model_dump()

    validate_portable_evidence_document(document)
    schema = json.loads(
        Path("engineering/specifications/portable-execution-evidence.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
    assert isinstance(document["trace"], dict)
    assert isinstance(document["trace"]["spans"][0], dict)
    assert evidence.json().startswith('{')

    restored = OmniCoreEvidenceAdapter().import_document(document)
    assert restored.execution_id == evidence.execution_id
    assert restored.trace["trace_id"] == "trace-portable"
    assert restored.trace["events"][0]["event_id"] == "event-portable-final"


def test_generic_adapter_preserves_timing_usage_errors_and_capture_state():
    payload = {
        "id": "vendor-rich",
        "status": "failed",
        "started_at": "2026-09-14T10:00:00+00:00",
        "ended_at": "2026-09-14T10:00:05+00:00",
        "execution": {
            "run_id": "vendor-rich-run",
            "source": "production",
            "surface": "production",
        },
        "provenance": {
            "source": "vendor",
            "application_version": "2.4.0",
            "vendor_case": "case-7",
        },
        "metadata": {"model": "vendor-model", "vendor_region": "eu"},
        "spans": [
            {
                "id": "vendor-root",
                "kind": "agent",
                "name": "root",
                "status": "success",
                "started_at": "2026-09-14T10:00:00+00:00",
                "ended_at": "2026-09-14T10:00:05+00:00",
                "duration_ms": 5000,
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                "event_ids": ["vendor-tool-error"],
            },
            {
                "id": "vendor-tool",
                "parent_id": "vendor-root",
                "kind": "tool",
                "name": "lookup",
                "status": "failed",
                "started_at": "2026-09-14T10:00:01+00:00",
                "ended_at": "2026-09-14T10:00:02+00:00",
                "duration": 1.0,
                "error": {"type": "upstream", "message": "service unavailable"},
                "input_capture": {
                    "state": "redacted",
                    "source": "tool",
                    "role": "tool_request",
                    "reason": "secret argument",
                },
            },
        ],
        "events": [
            {
                "id": "vendor-tool-error",
                "type": "tool_error",
                "span_id": "vendor-tool",
                "duration_ms": 1000,
                "usage": {"prompt_tokens": 2},
                "error": {"type": "upstream", "message": "service unavailable"},
                "output_capture": {
                    "state": "not_recorded",
                    "source": "tool",
                    "role": "tool_result",
                    "reason": "vendor policy",
                },
            },
        ],
    }

    evidence = GenericTraceEvidenceAdapter().import_trace(payload)
    serialized = evidence.model_dump()
    root = serialized["trace"]
    root_span = next(span for span in root["spans"] if span["span_id"] == "vendor-root")
    tool_span = next(span for span in root["spans"] if span["span_id"] == "vendor-tool")
    error_event = root["events"][0]

    assert root["started_at"] == "2026-09-14T10:00:00+00:00"
    assert root["ended_at"] == "2026-09-14T10:00:05+00:00"
    assert root["schema_version"] is None
    assert tool_span["duration_ms"] == 1000
    assert tool_span["error"]["message"] == "service unavailable"
    assert root_span["token_usage"]["total_tokens"] == 18
    assert tool_span["input_capture"]["state"] == "redacted"
    assert error_event["duration_ms"] == 1000
    assert error_event["timestamp"] is None
    assert error_event["output_capture"]["state"] == "not_recorded"
    assert root["metadata"]["extra"]["vendor_region"] == "eu"
    assert root["provenance"]["extra"]["vendor_case"] == "case-7"
    assert not any(item["type"] == "trace_status" for item in evidence.missing_evidence)
    assert any(
        item.get("type") == "event_output" and item.get("state") == "not_recorded"
        for item in evidence.missing_evidence
    )
    assert any(item["type"] == "event_timestamp" for item in evidence.missing_evidence)
    assert any(item["type"] == "span_schema_version" for item in evidence.missing_evidence)
    validate_portable_evidence_document(serialized)


def test_generic_adapter_maps_external_shape_without_discarding_unknown_events():
    payload = {
        "id": "vendor-trace",
        "status": "success",
        "execution": {
            "run_id": "vendor-run",
            "source": "production",
            "surface": "production",
        },
        "spans": [
            {"id": "root", "kind": "agent", "name": "root", "status": "success"},
            {
                "id": "tool",
                "parent_id": "root",
                "kind": "tool",
                "name": "lookup",
                "status": "success",
            },
        ],
        "events": [
            {"id": "vendor-event", "type": "vendor.delta", "span_id": "tool"},
            {
                "id": "vendor-final",
                "type": "final_answer",
                "span_id": "root",
                "output": {"response": "done"},
            },
        ],
    }

    evidence = GenericTraceEvidenceAdapter().import_trace(payload)

    assert evidence.execution_id == "vendor-run"
    assert evidence.source == "production"
    assert evidence.adapter == "generic"
    assert any(
        event["event_type"] == "vendor.delta" for event in evidence.trace["events"]
    )
    vendor_event = next(
        event for event in evidence.trace["events"] if event["event_id"] == "vendor-event"
    )
    assert vendor_event["metadata"]["experimental"] is True
    assert {span["kind"] for span in evidence.trace["spans"]} == {
        "agent.run",
        "tool.call",
    }


def test_evidence_adapter_rejects_duplicate_event_identity():
    trace = TelemetryTrace(
        trace_id="trace-duplicate",
        root_span_id="span-duplicate",
        spans=[
            TelemetrySpan(
                trace_id="trace-duplicate",
                span_id="span-duplicate",
                name="agent.run",
                kind="agent.run",
                actor=TelemetryActor(type=ActorType.AGENT),
            )
        ],
        events=[
            TelemetryEvent(
                trace_id="trace-duplicate",
                span_id="span-duplicate",
                event_id="same",
                event_type="agent_start",
                actor=TelemetryActor(type=ActorType.AGENT),
            ),
            TelemetryEvent(
                trace_id="trace-duplicate",
                span_id="span-duplicate",
                event_id="same",
                event_type="agent_end",
                actor=TelemetryActor(type=ActorType.AGENT),
            ),
        ],
    )

    with pytest.raises(EvidenceValidationError, match="duplicate event"):
        OmniCoreEvidenceAdapter().import_trace(trace)
