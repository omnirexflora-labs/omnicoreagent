from __future__ import annotations

from omnicoreagent.core.telemetry.models import (
    ActorType,
    TraceStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetryTrace,
    telemetry_id,
)


class TelemetryNormalizer:
    def normalize(self, trace: TelemetryTrace) -> TelemetryTrace:
        normalized = TelemetryTrace.from_dict(trace.model_dump())
        normalized.spans.sort(key=lambda span: (span.started_at, span.span_id))
        normalized.events.sort(
            key=lambda event: (event.sequence_number, event.timestamp, event.event_id)
        )
        self._mark_missing_references(normalized)
        self._mark_incomplete_trace(normalized)
        normalized.events.sort(
            key=lambda event: (event.sequence_number, event.timestamp, event.event_id)
        )
        return normalized

    def _mark_missing_references(self, trace: TelemetryTrace) -> None:
        span_ids = {span.span_id for span in trace.spans}
        event_ids = {event.event_id for event in trace.events}
        missing: list[dict[str, str]] = []

        if trace.root_span_id not in span_ids:
            missing.append({"type": "root_span", "id": trace.root_span_id})

        for span in trace.spans:
            if span.parent_span_id and span.parent_span_id not in span_ids:
                missing.append({"type": "parent_span", "id": span.parent_span_id})
            for event_id in span.event_ids:
                if event_id not in event_ids:
                    missing.append({"type": "span_event", "id": event_id})

        for event in trace.events:
            if event.span_id and event.span_id not in span_ids:
                missing.append({"type": "event_span", "id": event.span_id})
            if event.parent_event_id and event.parent_event_id not in event_ids:
                missing.append({"type": "parent_event", "id": event.parent_event_id})

        if missing:
            trace.metadata.tags = sorted({*trace.metadata.tags, "missing_evidence"})
            if any(event.metadata.get("normalizer") == "missing_evidence" for event in trace.events):
                return
            trace.events.append(
                TelemetryEvent(
                    event_id=telemetry_id("missing_evidence"),
                    trace_id=trace.trace_id,
                    sequence_number=_next_sequence(trace),
                    timestamp=trace.started_at,
                    event_type="runtime_error",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    output={"missing": missing},
                    metadata={"normalizer": "missing_evidence"},
                )
            )

    def _mark_incomplete_trace(self, trace: TelemetryTrace) -> None:
        if trace.status not in {TraceStatus.RUNNING, TraceStatus.PARTIAL} and trace.ended_at:
            return
        trace.metadata.tags = sorted({*trace.metadata.tags, "incomplete_trace"})
        if any(event.metadata.get("normalizer") == "incomplete_trace" for event in trace.events):
            return
        trace.events.append(
            TelemetryEvent(
                event_id=telemetry_id("incomplete_trace"),
                trace_id=trace.trace_id,
                sequence_number=_next_sequence(trace),
                timestamp=trace.started_at,
                event_type="final_state",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                output={"status": trace.status.value, "ended_at": trace.ended_at},
                metadata={"normalizer": "incomplete_trace"},
            )
        )


def _next_sequence(trace: TelemetryTrace) -> int:
    if not trace.events:
        return 1
    return max(event.sequence_number for event in trace.events) + 1
