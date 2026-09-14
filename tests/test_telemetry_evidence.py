from __future__ import annotations

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

    assert evidence.trace.evidence_status == "partial"
    assert any(item["state"] == "not_recorded" for item in evidence.missing_evidence)
    assert not hasattr(evidence, "score")


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
    assert any(event.event_type == "vendor.delta" for event in evidence.trace.events)
    vendor_event = next(
        event for event in evidence.trace.events if event.event_id == "vendor-event"
    )
    assert vendor_event.metadata["experimental"] is True
    assert {span.kind for span in evidence.trace.spans} == {"agent.run", "tool.call"}


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
