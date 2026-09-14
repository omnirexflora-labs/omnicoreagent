from __future__ import annotations

from omnicoreagent.core.telemetry.models import (
    ActorType,
    CaptureState,
    TraceEvidenceStatus,
    TraceStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetryTrace,
)


class TelemetryNormalizer:
    def normalize(self, trace: TelemetryTrace) -> TelemetryTrace:
        normalized = TelemetryTrace.from_dict(trace.model_dump())
        normalized.metadata.tags = sorted(set(normalized.metadata.tags))
        normalized.spans.sort(key=lambda span: (span.started_at, span.span_id))
        normalized.events.sort(
            key=lambda event: (event.sequence_number, event.timestamp, event.event_id)
        )
        self._mark_legacy_schema(normalized)
        self._mark_missing_references(normalized)
        self._mark_capture_gaps(normalized)
        self._mark_incomplete_trace(normalized)
        normalized.events.sort(
            key=lambda event: (event.sequence_number, event.timestamp, event.event_id)
        )
        _normalize_span_event_ids(normalized)
        return normalized

    def _mark_missing_references(self, trace: TelemetryTrace) -> None:
        span_ids = {span.span_id for span in trace.spans}
        event_ids = {event.event_id for event in trace.events}
        missing: list[dict[str, str]] = []

        if trace.root_span_id not in span_ids:
            missing.append({"type": "root_span", "id": trace.root_span_id})

        for span in trace.spans:
            if span.parent_span_id and span.parent_span_id not in span_ids:
                if (
                    span.span_id == trace.root_span_id
                    and trace.parent_trace_id is not None
                    and span.parent_span_id == trace.parent_span_id
                ):
                    continue
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
            trace.evidence_status = TraceEvidenceStatus.PARTIAL
            trace.metadata.tags = sorted({*trace.metadata.tags, "missing_evidence"})
            if any(event.metadata.get("normalizer") == "missing_evidence" for event in trace.events):
                return
            trace.events.append(
                TelemetryEvent(
                    event_id=_normalizer_event_id(trace, "missing_evidence"),
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
        trace.evidence_status = TraceEvidenceStatus.PARTIAL
        trace.metadata.tags = sorted({*trace.metadata.tags, "incomplete_trace"})
        if any(event.metadata.get("normalizer") == "incomplete_trace" for event in trace.events):
            return
        trace.events.append(
            TelemetryEvent(
                event_id=_normalizer_event_id(trace, "incomplete_trace"),
                trace_id=trace.trace_id,
                sequence_number=_next_sequence(trace),
                timestamp=trace.started_at,
                event_type="final_state",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                output={"status": trace.status.value, "ended_at": trace.ended_at},
                metadata={"normalizer": "incomplete_trace"},
            )
        )

    def _mark_capture_gaps(self, trace: TelemetryTrace) -> None:
        unavailable = {
            CaptureState.REDACTED,
            CaptureState.TRUNCATED,
            CaptureState.NOT_RECORDED,
            CaptureState.MISSING,
            CaptureState.INFERRED,
        }
        gaps: list[dict[str, str]] = []
        for record_type, records in (("span", trace.spans), ("event", trace.events)):
            for record in records:
                record_id = record.span_id if record_type == "span" else record.event_id
                for direction in ("input", "output"):
                    capture = getattr(record, f"{direction}_capture", None)
                    if capture is None:
                        continue
                    state = CaptureState(capture.state)
                    if state in unavailable or (
                        state == CaptureState.OFFLOADED and not capture.reference
                    ):
                        gaps.append(
                            {
                                "type": f"{record_type}_{direction}",
                                "id": record_id,
                                "state": state.value,
                            }
                        )
        if not gaps:
            return
        trace.evidence_status = TraceEvidenceStatus.PARTIAL
        trace.metadata.tags = sorted({*trace.metadata.tags, "capture_gaps"})
        if any(event.metadata.get("normalizer") == "capture_gaps" for event in trace.events):
            return
        trace.events.append(
            TelemetryEvent(
                event_id=_normalizer_event_id(trace, "capture_gaps"),
                trace_id=trace.trace_id,
                sequence_number=_next_sequence(trace),
                timestamp=trace.started_at,
                event_type="runtime_error",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                output={"capture_gaps": gaps},
                metadata={"normalizer": "capture_gaps"},
            )
        )

    def _mark_legacy_schema(self, trace: TelemetryTrace) -> None:
        legacy = trace.schema_version < 3 or any(
            record.schema_version < 3 for record in (*trace.spans, *trace.events)
        )
        if not legacy:
            return
        trace.evidence_status = TraceEvidenceStatus.UNKNOWN
        trace.metadata.tags = sorted({*trace.metadata.tags, "legacy_schema"})


def _next_sequence(trace: TelemetryTrace) -> int:
    if not trace.events:
        return 1
    return max(event.sequence_number for event in trace.events) + 1


def _normalize_span_event_ids(trace: TelemetryTrace) -> None:
    event_order = {
        event.event_id: index
        for index, event in enumerate(trace.events)
    }
    for span in trace.spans:
        deduped_ids = list(dict.fromkeys(span.event_ids))
        span.event_ids = sorted(
            deduped_ids,
            key=lambda event_id: (
                event_order.get(event_id, len(event_order)),
                deduped_ids.index(event_id),
            ),
        )


def _normalizer_event_id(trace: TelemetryTrace, reason: str) -> str:
    base = f"event_normalizer_{trace.trace_id}_{reason}"
    existing_ids = {event.event_id for event in trace.events}
    if base not in existing_ids:
        return base
    index = 1
    while f"{base}_{index}" in existing_ids:
        index += 1
    return f"{base}_{index}"
