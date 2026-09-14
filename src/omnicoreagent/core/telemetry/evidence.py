"""Portable execution-evidence adapters.

This module only imports and validates execution facts. It deliberately has no
evaluator, score, or release-decision behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from omnicoreagent.core.telemetry.models import (
    ActorType,
    CaptureState,
    FOUNDATION_EVENT_TYPES,
    FOUNDATION_SPAN_KINDS,
    SpanStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetryProvenance,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
)
from omnicoreagent.core.telemetry.normalizer import TelemetryNormalizer


class EvidenceValidationError(ValueError):
    """Raised when an adapter cannot preserve a trace's causal identity."""


@dataclass(frozen=True)
class EvidenceReference:
    kind: str
    identifier: str
    role: str | None = None

    def model_dump(self) -> dict[str, str]:
        payload = {"kind": self.kind, "id": self.identifier}
        if self.role is not None:
            payload["role"] = self.role
        return payload


@dataclass
class PortableExecutionEvidence:
    """An evaluator-facing view over one normalized execution graph."""

    execution_id: str
    source: str
    trace: TelemetryTrace
    task: dict[str, Any] | None = None
    final_output_references: tuple[EvidenceReference, ...] = ()
    missing_evidence: tuple[dict[str, Any], ...] = ()
    adapter: str = "omnicoreagent"
    schema_version: int = 1
    facts: tuple[EvidenceReference, ...] = field(default_factory=tuple)

    def model_dump(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "source": self.source,
            "adapter": self.adapter,
            "task": self.task,
            "trace": self.trace.model_dump(),
            "final_output_references": [
                reference.model_dump() for reference in self.final_output_references
            ],
            "missing_evidence": list(self.missing_evidence),
            "facts": [reference.model_dump() for reference in self.facts],
        }


class OmniCoreEvidenceAdapter:
    """Import traces emitted by OmniCoreAgent's built-in recorder."""

    name = "omnicoreagent"

    def import_trace(
        self,
        value: TelemetryTrace | Mapping[str, Any],
        *,
        task: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> PortableExecutionEvidence:
        trace = value if isinstance(value, TelemetryTrace) else TelemetryTrace.from_dict(dict(value))
        normalized = TelemetryNormalizer().normalize(trace)
        _validate_trace(normalized)
        selected_source = source or _source_for_trace(normalized)
        if not selected_source.strip():
            raise EvidenceValidationError("evidence source must not be empty")
        final_outputs = tuple(
            EvidenceReference(kind="event", identifier=event.event_id, role="final_output")
            for event in normalized.events
            if event.event_type == "final_answer"
        )
        missing = tuple(_missing_evidence(normalized))
        if normalized.status == TraceStatus.COMPLETED and not final_outputs:
            missing += ({"type": "final_output", "reason": "no final_answer event"},)
        facts = tuple(
            EvidenceReference(kind="event", identifier=event.event_id)
            for event in normalized.events
            if not event.metadata.get("normalizer")
        )
        return PortableExecutionEvidence(
            execution_id=normalized.run_id or normalized.trace_id,
            source=selected_source,
            trace=normalized,
            task=dict(task) if task is not None else None,
            final_output_references=final_outputs,
            missing_evidence=missing,
            adapter=self.name,
            facts=facts,
        )


class GenericTraceEvidenceAdapter(OmniCoreEvidenceAdapter):
    """Import a small vendor-neutral external trace shape.

    The adapter is intentionally conservative: known OmniCoreAgent identities
    are retained, unknown event types remain experimental facts, and unsupported
    span kinds are represented as runtime-control spans with their original kind
    in attributes rather than being silently discarded.
    """

    name = "generic"

    def import_trace(
        self,
        value: TelemetryTrace | Mapping[str, Any],
        *,
        task: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> PortableExecutionEvidence:
        if isinstance(value, TelemetryTrace):
            return super().import_trace(value, task=task, source=source)
        raw = dict(value)
        trace_id = str(raw.get("trace_id") or raw.get("id") or "").strip()
        raw_spans = list(raw.get("spans") or [])
        root_span_id = str(
            raw.get("root_span_id")
            or (raw_spans[0].get("id") if raw_spans else "")
            or ""
        ).strip()
        if not trace_id or not root_span_id:
            raise EvidenceValidationError(
                "external trace requires trace_id/id and at least one span"
            )
        spans = [_map_external_span(item, trace_id) for item in raw_spans]
        events = [
            _map_external_event(item, trace_id, root_span_id)
            for item in list(raw.get("events") or [])
        ]
        execution = raw.get("execution")
        execution = execution if isinstance(execution, Mapping) else {}
        raw_provenance = raw.get("provenance")
        provenance = (
            dict(raw_provenance) if isinstance(raw_provenance, Mapping) else {}
        )
        provenance.setdefault("source", str(execution.get("source") or "external"))
        provenance.setdefault("adapter", self.name)
        provenance.setdefault("external_ids", {"trace_id": trace_id})
        trace = TelemetryTrace(
            trace_id=trace_id,
            root_span_id=root_span_id,
            parent_trace_id=raw.get("parent_trace_id"),
            parent_span_id=raw.get("parent_span_id"),
            status=_map_trace_status(raw.get("status")),
            run_id=raw.get("run_id") or execution.get("run_id"),
            session_id=raw.get("session_id") or execution.get("session_id"),
            task_id=raw.get("task_id") or execution.get("task_id"),
            agent_id=raw.get("agent_id") or execution.get("agent_id"),
            execution_surface=str(
                raw.get("execution_surface") or execution.get("surface") or "external"
            ),
            provenance=TelemetryProvenance.from_dict(provenance),
            spans=spans,
            events=events,
        )
        return super().import_trace(trace, task=task, source=source)


def _source_for_trace(trace: TelemetryTrace) -> str:
    if (
        trace.execution_surface == "controlled"
        or trace.provenance.source == "controlled"
        or trace.provenance.adapter == "harbor"
    ):
        return "controlled"
    return "production"


def _validate_trace(trace: TelemetryTrace) -> None:
    if not trace.trace_id or not trace.root_span_id:
        raise EvidenceValidationError("trace and root span identifiers are required")
    span_ids = [span.span_id for span in trace.spans]
    event_ids = [event.event_id for event in trace.events]
    if any(not identifier for identifier in span_ids):
        raise EvidenceValidationError("trace contains a span without an identifier")
    if any(not identifier for identifier in event_ids):
        raise EvidenceValidationError("trace contains an event without an identifier")
    if len(span_ids) != len(set(span_ids)):
        raise EvidenceValidationError("trace contains duplicate span identifiers")
    if len(event_ids) != len(set(event_ids)):
        raise EvidenceValidationError("trace contains duplicate event identifiers")
    span_set = set(span_ids)
    event_set = set(event_ids)
    if trace.root_span_id not in span_set:
        raise EvidenceValidationError("trace root span is absent")
    for span in trace.spans:
        if span.trace_id != trace.trace_id:
            raise EvidenceValidationError(f"span {span.span_id} has a different trace_id")
        if span.parent_span_id and span.parent_span_id not in span_set:
            is_external_root = (
                span.span_id == trace.root_span_id
                and trace.parent_trace_id is not None
                and span.parent_span_id == trace.parent_span_id
            )
            if not is_external_root:
                raise EvidenceValidationError(
                    f"span {span.span_id} references an unknown parent span"
                )
        if any(event_id not in event_set for event_id in span.event_ids):
            raise EvidenceValidationError(f"span {span.span_id} references an unknown event")
    for event in trace.events:
        if event.trace_id != trace.trace_id:
            raise EvidenceValidationError(f"event {event.event_id} has a different trace_id")
        if event.span_id and event.span_id not in span_set:
            raise EvidenceValidationError(f"event {event.event_id} references an unknown span")
        if event.parent_event_id and event.parent_event_id not in event_set:
            raise EvidenceValidationError(
                f"event {event.event_id} references an unknown parent event"
            )


def _missing_evidence(trace: TelemetryTrace) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for event in trace.events:
        normalizer = event.metadata.get("normalizer")
        output = event.output or {}
        if normalizer == "missing_evidence":
            missing.extend(output.get("missing", []))
        elif normalizer == "capture_gaps":
            missing.extend(output.get("capture_gaps", []))
        elif normalizer == "incomplete_trace":
            missing.append({"type": "trace", "reason": "trace did not finish"})
    for record_type, records in (("span", trace.spans), ("event", trace.events)):
        for record in records:
            record_id = record.span_id if record_type == "span" else record.event_id
            for direction in ("input", "output"):
                capture = getattr(record, f"{direction}_capture", None)
                if capture is None:
                    continue
                state = CaptureState(capture.state)
                if state in {
                    CaptureState.REDACTED,
                    CaptureState.TRUNCATED,
                    CaptureState.NOT_RECORDED,
                    CaptureState.MISSING,
                    CaptureState.INFERRED,
                } or (state == CaptureState.OFFLOADED and not capture.reference):
                    missing.append(
                        {"type": f"{record_type}_{direction}", "id": record_id, "state": state.value}
                    )
    return _dedupe_dicts(missing)


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = repr(sorted(value.items()))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _map_external_span(raw: Mapping[str, Any], trace_id: str) -> TelemetrySpan:
    original_kind = str(raw.get("kind") or raw.get("type") or "runtime.control")
    kind_map = {
        "agent": "agent.run",
        "llm": "model.call",
        "model": "model.call",
        "tool": "tool.call",
    }
    kind = kind_map.get(original_kind, original_kind)
    if kind not in FOUNDATION_SPAN_KINDS:
        kind = "runtime.control"
    attributes = dict(raw.get("attributes") or {})
    if kind == "runtime.control" and original_kind != kind:
        attributes.setdefault("external_span_kind", original_kind)
    return TelemetrySpan(
        trace_id=trace_id,
        span_id=str(raw.get("span_id") or raw.get("id") or "").strip(),
        parent_span_id=raw.get("parent_span_id") or raw.get("parent_id"),
        name=str(raw.get("name") or original_kind),
        kind=kind,
        actor=_map_actor(raw.get("actor"), original_kind),
        status=_map_span_status(raw.get("status")),
        input=_mapping(raw.get("input")),
        output=_mapping(raw.get("output")),
        attributes=attributes,
        event_ids=list(raw.get("event_ids") or []),
    )


def _map_external_event(
    raw: Mapping[str, Any], trace_id: str, root_span_id: str
) -> TelemetryEvent:
    event_type = str(raw.get("event_type") or raw.get("type") or "runtime_error")
    metadata = dict(raw.get("metadata") or {})
    if event_type not in FOUNDATION_EVENT_TYPES:
        metadata.setdefault("experimental", True)
        metadata.setdefault("external_event_type", event_type)
    return TelemetryEvent(
        trace_id=trace_id,
        event_id=str(raw.get("event_id") or raw.get("id") or "").strip(),
        span_id=raw.get("span_id") or root_span_id,
        parent_event_id=raw.get("parent_event_id") or raw.get("parent_id"),
        event_type=event_type,
        actor=_map_actor(raw.get("actor"), "event"),
        input=_mapping(raw.get("input")),
        output=_mapping(raw.get("output")),
        metadata=metadata,
    )


def _mapping(value: Any) -> dict[str, Any] | None:
    if value is None or isinstance(value, dict):
        return value
    return {"value": value}


def _map_actor(value: Any, fallback: str) -> TelemetryActor:
    if isinstance(value, Mapping):
        actor_type = str(value.get("type") or "system")
        try:
            actor = ActorType(actor_type)
        except ValueError:
            actor = ActorType.SYSTEM
        return TelemetryActor(type=actor, id=value.get("id"), name=value.get("name"))
    return TelemetryActor(type=ActorType.SYSTEM, name=fallback)


def _map_span_status(value: Any) -> SpanStatus:
    value = str(value or "running").lower()
    return {
        "success": SpanStatus.OK,
        "completed": SpanStatus.OK,
        "ok": SpanStatus.OK,
        "failed": SpanStatus.ERROR,
        "error": SpanStatus.ERROR,
        "cancelled": SpanStatus.CANCELLED,
        "timeout": SpanStatus.TIMEOUT,
    }.get(value, SpanStatus.RUNNING)


def _map_trace_status(value: Any) -> TraceStatus:
    value = str(value or "running").lower()
    return {
        "success": TraceStatus.COMPLETED,
        "completed": TraceStatus.COMPLETED,
        "ok": TraceStatus.COMPLETED,
        "failed": TraceStatus.FAILED,
        "error": TraceStatus.FAILED,
        "cancelled": TraceStatus.CANCELLED,
        "timeout": TraceStatus.TIMEOUT,
        "partial": TraceStatus.PARTIAL,
    }.get(value, TraceStatus.RUNNING)
