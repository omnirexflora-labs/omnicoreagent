"""Portable execution-evidence adapters.

This module only imports and validates execution facts. It deliberately has no
evaluator, score, or release-decision behavior.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any, Mapping

from omnicoreagent.core.telemetry.models import (
    ActorType,
    CaptureState,
    FOUNDATION_EVENT_TYPES,
    FOUNDATION_SPAN_KINDS,
    SpanStatus,
    TelemetryActor,
    TelemetryCapture,
    TelemetryEvent,
    TelemetryProvenance,
    TelemetrySpan,
    TelemetryTrace,
    TokenUsage,
    TraceEvidenceStatus,
    TraceStatus,
    to_plain,
)
from omnicoreagent.core.telemetry.normalizer import TelemetryNormalizer


class EvidenceValidationError(ValueError):
    """Raised when an adapter cannot preserve a trace's causal identity."""


PORTABLE_EVIDENCE_SCHEMA = "omnicoreagent.execution-evidence"
PORTABLE_EVIDENCE_SCHEMA_VERSION = 1
PORTABLE_EVIDENCE_CONTRACT = (
    f"{PORTABLE_EVIDENCE_SCHEMA}/v{PORTABLE_EVIDENCE_SCHEMA_VERSION}"
)


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
    # ``trace`` is the serialized contract, never an internal TelemetryTrace.
    trace: dict[str, Any]
    task: dict[str, Any] | None = None
    final_output_references: tuple[EvidenceReference, ...] = ()
    missing_evidence: tuple[dict[str, Any], ...] = ()
    adapter: str = "omnicoreagent"
    schema_version: int = 1
    facts: tuple[EvidenceReference, ...] = field(default_factory=tuple)
    _normalized_trace: TelemetryTrace | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def internal_trace(self) -> TelemetryTrace | None:
        """Return the runtime trace for adapter internals only.

        Evaluators and integrations should use ``trace`` or ``model_dump``.
        This accessor exists for the runtime while the independent serialized
        contract is introduced.
        """

        return self._normalized_trace

    def model_dump(self) -> dict[str, Any]:
        return {
            "contract": PORTABLE_EVIDENCE_CONTRACT,
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "source": self.source,
            "adapter": self.adapter,
            "task": _json_value(self.task),
            "trace": _json_value(self.trace),
            "final_output_references": [
                reference.model_dump() for reference in self.final_output_references
            ],
            "missing_evidence": _json_value(list(self.missing_evidence)),
            "facts": [reference.model_dump() for reference in self.facts],
        }

    def json(self, *, indent: int | None = None) -> str:
        """Serialize the evidence using only JSON-native values."""

        return json.dumps(self.model_dump(), sort_keys=True, indent=indent)


def _json_value(value: Any) -> Any:
    """Return a value that can cross the standalone JSON boundary."""

    plain = to_plain(value)
    try:
        return json.loads(json.dumps(plain, default=str))
    except (TypeError, ValueError):
        return str(plain)


def _serialize_trace(trace: TelemetryTrace) -> dict[str, Any]:
    """Serialize a trace while retaining unknown external fields as null."""

    payload = trace.model_dump()
    trace_missing = set(trace.metadata.extra.get("external_missing_fields", []))
    for field_name in trace_missing:
        payload[field_name.removeprefix("trace_")] = None
    for span in payload.get("spans", []):
        missing = set(span.get("attributes", {}).get("external_missing_fields", []))
        for field_name in missing:
            if field_name != "timestamp":
                span[_SERIALIZED_FIELD.get(field_name, field_name)] = None
    for event in payload.get("events", []):
        missing = set(event.get("metadata", {}).get("external_missing_fields", []))
        if "timestamp" in missing:
            event["timestamp"] = None
    return payload


def validate_portable_evidence_document(
    value: Mapping[str, Any],
) -> None:
    """Validate the standalone evidence envelope without runtime models.

    This deliberately checks mappings, identifiers, and relationships instead
    of constructing OmniCoreAgent telemetry objects. It is suitable for
    contract tests and documents the minimum an external evaluator must read.
    """

    if not isinstance(value, Mapping):
        raise EvidenceValidationError("portable evidence must be an object")
    _validate_against_schema(value)
    if value.get("contract") != PORTABLE_EVIDENCE_CONTRACT:
        raise EvidenceValidationError(
            f"portable evidence contract must be {PORTABLE_EVIDENCE_CONTRACT}"
        )
    if value.get("schema_version") != PORTABLE_EVIDENCE_SCHEMA_VERSION:
        raise EvidenceValidationError("unsupported portable evidence schema version")
    for key in ("execution_id", "source", "adapter"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise EvidenceValidationError(f"portable evidence requires {key}")
    trace = value.get("trace")
    if not isinstance(trace, Mapping):
        raise EvidenceValidationError("portable evidence trace must be an object")
    trace_id = trace.get("trace_id")
    root_span_id = trace.get("root_span_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise EvidenceValidationError("portable trace requires trace_id")
    if not isinstance(root_span_id, str) or not root_span_id:
        raise EvidenceValidationError("portable trace requires root_span_id")
    spans = trace.get("spans")
    events = trace.get("events")
    if not isinstance(spans, list) or not isinstance(events, list):
        raise EvidenceValidationError("portable trace requires spans and events arrays")
    span_ids = [_required_identifier(span, "span_id") for span in spans]
    event_ids = [_required_identifier(event, "event_id") for event in events]
    if len(span_ids) != len(set(span_ids)):
        raise EvidenceValidationError("portable trace contains duplicate span IDs")
    if len(event_ids) != len(set(event_ids)):
        raise EvidenceValidationError("portable trace contains duplicate event IDs")
    if root_span_id not in set(span_ids):
        raise EvidenceValidationError("portable trace root span is absent")
    span_set = set(span_ids)
    event_set = set(event_ids)
    parent_trace_id = trace.get("parent_trace_id")
    parent_span_id = trace.get("parent_span_id")
    for span in spans:
        if not isinstance(span, Mapping):
            raise EvidenceValidationError("portable span must be an object")
        if span.get("trace_id") != trace_id:
            raise EvidenceValidationError("portable span trace_id mismatch")
        parent = span.get("parent_span_id")
        if parent is not None and not isinstance(parent, str):
            raise EvidenceValidationError("portable span parent_span_id must be a string")
        if parent and parent not in span_set:
            external_root = (
                span.get("span_id") == root_span_id
                and parent_trace_id
                and parent == parent_span_id
            )
            if not external_root:
                raise EvidenceValidationError(
                    f"portable span {span.get('span_id')} references unknown parent"
                )
        event_ids_for_span = span.get("event_ids") or []
        if not isinstance(event_ids_for_span, list):
            raise EvidenceValidationError("portable span event_ids must be an array")
        for event_id in event_ids_for_span:
            if not isinstance(event_id, str):
                raise EvidenceValidationError("portable span event ID must be a string")
            if event_id not in event_set:
                raise EvidenceValidationError(
                    f"portable span {span.get('span_id')} references unknown event"
                )
    for event in events:
        if not isinstance(event, Mapping):
            raise EvidenceValidationError("portable event must be an object")
        if event.get("trace_id") != trace_id:
            raise EvidenceValidationError("portable event trace_id mismatch")
        span_id = event.get("span_id")
        if span_id is not None and not isinstance(span_id, str):
            raise EvidenceValidationError("portable event span_id must be a string")
        if span_id and span_id not in span_set:
            raise EvidenceValidationError("portable event references unknown span")
        parent_event_id = event.get("parent_event_id")
        if parent_event_id is not None and not isinstance(parent_event_id, str):
            raise EvidenceValidationError(
                "portable event parent_event_id must be a string"
            )
        if parent_event_id and parent_event_id not in event_set:
            raise EvidenceValidationError(
                "portable event references unknown parent event"
            )
    known = {"event": event_set, "span": span_set}
    for key in ("facts", "final_output_references"):
        for reference in value.get(key) or []:
            identifiers = known.get(reference.get("kind"))
            if identifiers is not None and reference.get("id") not in identifiers:
                raise EvidenceValidationError(
                    f"portable {key} references unknown {reference.get('kind')} "
                    f"{reference.get('id')}"
                )


@lru_cache(maxsize=1)
def portable_evidence_schema() -> dict[str, Any]:
    """The published JSON Schema for ``omnicoreagent.execution-evidence/v1``."""
    return json.loads(
        files("omnicoreagent.core.telemetry")
        .joinpath("schemas/portable-execution-evidence.schema.json")
        .read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def _schema_validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(portable_evidence_schema())


def _validate_against_schema(value: Mapping[str, Any]) -> None:
    errors = sorted(
        _schema_validator().iter_errors(value), key=lambda error: list(error.path)
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.path) or "(document)"
        raise EvidenceValidationError(
            f"portable evidence does not match its schema at {location}: {error.message}"
        )


def _required_identifier(value: Any, key: str) -> str:
    if not isinstance(value, Mapping) or not isinstance(value.get(key), str):
        raise EvidenceValidationError(f"portable record requires {key}")
    identifier = value[key].strip()
    if not identifier:
        raise EvidenceValidationError(f"portable record requires {key}")
    return identifier


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
        if not isinstance(value, TelemetryTrace) and _is_portable_document(value):
            return self.import_document(value, task=task)
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
            trace=_serialize_trace(normalized),
            task=dict(task) if task is not None else None,
            final_output_references=final_outputs,
            missing_evidence=missing,
            adapter=self.name,
            facts=facts,
            _normalized_trace=normalized,
        )

    def import_document(
        self,
        value: Mapping[str, Any],
        *,
        task: dict[str, Any] | None = None,
    ) -> PortableExecutionEvidence:
        """Re-import a previously serialized portable document."""

        validate_portable_evidence_document(value)
        document_task = task if task is not None else value.get("task")
        raw_trace = _json_value(value["trace"])
        # The portable contract intentionally allows null status, timestamps,
        # and schema versions when the producer cannot establish those facts.
        # Runtime models remain strict, so use placeholders only for internal
        # validation.  The returned evidence keeps the original JSON trace and
        # envelope metadata exactly as supplied.
        try:
            runtime_trace = _portable_trace_for_runtime(raw_trace)
            normalized = TelemetryNormalizer().normalize(
                TelemetryTrace.from_dict(runtime_trace)
            )
        except EvidenceValidationError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise EvidenceValidationError(
                f"portable trace cannot be read: {exc}"
            ) from exc
        _validate_trace(normalized)
        missing_evidence = list(_json_value(value["missing_evidence"]))
        recomputed = _missing_evidence(normalized)
        if raw_trace.get("evidence_status") == "complete" and recomputed:
            # A document cannot claim complete evidence its own records deny.
            raw_trace["evidence_status"] = "partial"
            missing_evidence = _dedupe_dicts(
                [
                    *missing_evidence,
                    {
                        "type": "evidence_status_claim",
                        "claimed": "complete",
                        "recomputed": "partial",
                    },
                    *recomputed,
                ]
            )
        final_outputs = tuple(
            EvidenceReference(
                kind=reference["kind"],
                identifier=reference["id"],
                role=reference.get("role"),
            )
            for reference in value["final_output_references"]
        )
        facts = tuple(
            EvidenceReference(
                kind=reference["kind"],
                identifier=reference["id"],
                role=reference.get("role"),
            )
            for reference in value["facts"]
        )
        return PortableExecutionEvidence(
            execution_id=value["execution_id"],
            source=value["source"],
            adapter=value["adapter"],
            schema_version=value["schema_version"],
            task=(
                dict(document_task)
                if isinstance(document_task, Mapping)
                else None
            ),
            trace=raw_trace,
            final_output_references=final_outputs,
            missing_evidence=tuple(missing_evidence),
            facts=facts,
            _normalized_trace=normalized,
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
        if not isinstance(value, TelemetryTrace) and _is_portable_document(value):
            return self.import_document(value, task=task)
        if isinstance(value, TelemetryTrace):
            return super().import_trace(value, task=task, source=source)
        raw = copy.deepcopy(dict(value))
        trace_id = str(raw.get("trace_id") or raw.get("id") or "").strip()
        raw_spans = list(raw.get("spans") or [])
        raw_events = list(raw.get("events") or [])
        if any(not isinstance(item, Mapping) for item in raw_spans):
            raise EvidenceValidationError("external spans must be objects")
        if any(not isinstance(item, Mapping) for item in raw_events):
            raise EvidenceValidationError("external events must be objects")
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
            for item in raw_events
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
        provenance = _preserve_extra(
            provenance,
            raw_provenance if isinstance(raw_provenance, Mapping) else {},
            {
                "source",
                "adapter",
                "application_version",
                "deployment_id",
                "environment",
                "evaluation_id",
                "case_id",
                "trial_id",
                "environment_id",
                "verifier_reference",
                "external_ids",
                "extra",
            },
        )
        metadata = _map_trace_metadata(raw.get("metadata"))
        if raw.get("status") is not None and not _known_trace_status(
            raw.get("status")
        ):
            metadata.setdefault("extra", {})["external_status"] = raw.get("status")
        trace = TelemetryTrace(
            trace_id=trace_id,
            root_span_id=root_span_id,
            parent_trace_id=raw.get("parent_trace_id"),
            parent_span_id=raw.get("parent_span_id"),
            status=_map_trace_status(raw.get("status")),
            incomplete=bool(raw.get("incomplete", False)),
            started_at=raw.get("started_at") or raw.get("start_time"),
            ended_at=raw.get("ended_at") or raw.get("end_time"),
            run_id=raw.get("run_id") or execution.get("run_id"),
            session_id=raw.get("session_id") or execution.get("session_id"),
            task_id=raw.get("task_id") or execution.get("task_id"),
            suite_id=raw.get("suite_id") or execution.get("suite_id"),
            agent_id=raw.get("agent_id") or execution.get("agent_id"),
            workflow_id=raw.get("workflow_id") or execution.get("workflow_id"),
            metadata=metadata,
            schema_version=_positive_int(raw.get("schema_version"), default=1),
            evidence_status=_map_evidence_status(raw.get("evidence_status")),
            execution_surface=str(
                raw.get("execution_surface") or execution.get("surface") or "external"
            ),
            provenance=TelemetryProvenance.from_dict(provenance),
            spans=spans,
            events=events,
        )
        evidence = super().import_trace(trace, task=task, source=source)
        missing = _external_missing_evidence(raw, raw_spans, raw_events)
        if missing:
            normalized = evidence.internal_trace or trace
            _mark_external_missing(normalized, missing)
            # Unknown or inferred fields mean the evidence is not complete,
            # whatever the external producer claimed.
            normalized.evidence_status = TraceEvidenceStatus.PARTIAL
            evidence.trace = _serialize_trace(normalized)
            evidence._normalized_trace = normalized
            evidence.missing_evidence = tuple(
                _dedupe_dicts([*evidence.missing_evidence, *missing])
            )
        return evidence


def _source_for_trace(trace: TelemetryTrace) -> str:
    if (
        trace.execution_surface == "controlled"
        or trace.provenance.source == "controlled"
        or trace.provenance.adapter == "harbor"
    ):
        return "controlled"
    return "production"


def _is_portable_document(value: Mapping[str, Any]) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("contract") == PORTABLE_EVIDENCE_CONTRACT
        and isinstance(value.get("trace"), Mapping)
    )


def _portable_trace_for_runtime(value: Mapping[str, Any]) -> dict[str, Any]:
    """Make nullable portable fields acceptable to strict runtime models.

    ``null`` in the serialized contract means that the producer could not
    establish a value.  It must never become a newly invented fact in the
    exported evidence, so this conversion is used only for validation and the
    private ``internal_trace`` accessor.
    """

    trace = _known_fields(TelemetryTrace, value)
    if trace.get("status") is None or not _known_trace_status(trace.get("status")):
        trace["status"] = TraceStatus.RUNNING.value
    if not _valid_schema_version(trace.get("schema_version")):
        trace["schema_version"] = 1
    if trace.get("evidence_status") not in {
        "complete",
        "partial",
        "unknown",
    }:
        trace["evidence_status"] = "unknown"

    runtime_spans = []
    for raw_span in trace.get("spans", []):
        span = _known_fields(TelemetrySpan, raw_span)
        if span.get("kind") not in FOUNDATION_SPAN_KINDS:
            attributes = dict(span.get("attributes") or {})
            attributes.setdefault("external_span_kind", span.get("kind"))
            span["attributes"] = attributes
            span["kind"] = "runtime.control"
        span["actor"] = _known_actor(span.get("actor"))
        if span.get("status") is None or not _known_span_status(span.get("status")):
            span["status"] = SpanStatus.RUNNING.value
        if not _valid_schema_version(span.get("schema_version")):
            span["schema_version"] = 1
        runtime_spans.append(span)
    trace["spans"] = runtime_spans

    runtime_events = []
    for raw_event in trace.get("events", []):
        event = _known_fields(TelemetryEvent, raw_event)
        event["actor"] = _known_actor(event.get("actor"))
        if not _valid_schema_version(event.get("schema_version")):
            event["schema_version"] = 1
        if event.get("event_type") not in FOUNDATION_EVENT_TYPES:
            metadata = dict(event.get("metadata") or {})
            metadata["experimental"] = True
            event["metadata"] = metadata
        runtime_events.append(event)
    trace["events"] = runtime_events
    return trace


def _known_fields(model: type, value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the fields a runtime model accepts; the portable copy keeps the rest."""
    names = {item.name for item in fields(model)}
    return {key: item for key, item in value.items() if key in names}


def _known_actor(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    actor = dict(value)
    try:
        ActorType(actor.get("type"))
    except ValueError:
        actor["type"] = ActorType.SYSTEM.value
    return actor


def _map_trace_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metadata = dict(value)
    known = {
        "agent_name",
        "agent_version",
        "model_provider",
        "model",
        "prompt_version",
        "tool_schema_version",
        "memory_config_version",
        "constraint_config_version",
        "guardrail_mode",
        "guardrail_config_version",
        "privacy_config_version",
        "telemetry_config_version",
        "telemetry_storage",
        "telemetry_payload_storage",
        "tags",
        "extra",
    }
    return _preserve_extra(metadata, value, known)


def _preserve_extra(
    target: dict[str, Any],
    raw: Mapping[str, Any],
    known: set[str],
) -> dict[str, Any]:
    extra = dict(target.get("extra") or {})
    extra.update({key: value for key, value in raw.items() if key not in known})
    if extra:
        target["extra"] = extra
    return target


def _positive_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _duration_ms(raw: Mapping[str, Any]) -> int | None:
    value = raw.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = raw.get("duration")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value) * 1000
    return max(0, int(round(value)))


def _map_error(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        error = dict(value)
        error.setdefault("type", "external_error")
        error.setdefault("message", "external execution error")
        return error
    return {"type": "external_error", "message": str(value)}


def _map_usage(value: Any) -> TokenUsage | dict[str, Any]:
    if not isinstance(value, Mapping):
        return TokenUsage()
    usage = dict(value)
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
        total = prompt + completion
    return TokenUsage(
        prompt_tokens=_int_or_none(prompt),
        completion_tokens=_int_or_none(completion),
        total_tokens=_int_or_none(total),
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _map_capture(value: Any) -> TelemetryCapture | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return TelemetryCapture(
            state=CaptureState.MISSING,
            source="adapter",
            role="external",
            reason="external capture descriptor was not an object",
        )
    raw_state = str(value.get("state") or CaptureState.MISSING.value)
    try:
        state = CaptureState(raw_state)
        reason = value.get("reason")
    except ValueError:
        state = CaptureState.MISSING
        reason = f"unsupported external capture state: {raw_state}"
    return TelemetryCapture(
        state=state,
        source=str(value.get("source") or "adapter"),
        role=str(value.get("role") or "external"),
        reference=value.get("reference"),
        content_type=value.get("content_type"),
        checksum=value.get("checksum"),
        original_bytes=_int_or_none(value.get("original_bytes")),
        recorded_bytes=_int_or_none(value.get("recorded_bytes")),
        policy_version=value.get("policy_version"),
        reason=reason
        or (
            "external capture descriptor has no reason"
            if state
            in {
                CaptureState.NOT_RECORDED,
                CaptureState.MISSING,
                CaptureState.TRUNCATED,
                CaptureState.INFERRED,
            }
            else None
        ),
    )


def _map_evidence_status(value: Any) -> str:
    value = str(value or "unknown").lower()
    return value if value in {"complete", "partial", "unknown"} else "unknown"


def _external_missing_evidence(
    raw_trace: Mapping[str, Any],
    raw_spans: list[Any],
    raw_events: list[Any],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    trace_fields = {
        "status": ("status",),
        "started_at": ("started_at", "start_time"),
        "ended_at": ("ended_at", "end_time"),
        "schema_version": ("schema_version",),
    }
    for field_name, aliases in trace_fields.items():
        if not any(raw_trace.get(alias) is not None for alias in aliases):
            missing.append({"type": f"trace_{field_name}", "state": "unknown"})
    if raw_trace.get("schema_version") is not None and not _valid_schema_version(
        raw_trace.get("schema_version")
    ):
        missing.append({"type": "trace_schema_version", "state": "unknown"})
    if raw_trace.get("status") is not None and not _known_trace_status(
        raw_trace.get("status")
    ):
        missing.append({"type": "trace_status", "state": "unknown"})
    for record_type, records, identifier_keys in (
        ("span", raw_spans, ("span_id", "id")),
        ("event", raw_events, ("event_id", "id")),
    ):
        for index, raw in enumerate(records):
            raw = raw if isinstance(raw, Mapping) else {}
            identifier = next(
                (str(raw.get(key)) for key in identifier_keys if raw.get(key)),
                f"{record_type}_{index}",
            )
            fields = {
                "status": ("status",),
                "started_at": ("started_at", "start_time"),
                "ended_at": ("ended_at", "end_time"),
                "schema_version": ("schema_version",),
            }
            if record_type == "event":
                fields = {
                    "timestamp": ("timestamp", "started_at"),
                    "schema_version": ("schema_version",),
                }
            for field_name, aliases in fields.items():
                if not any(raw.get(alias) is not None for alias in aliases):
                    missing.append(
                        {"type": f"{record_type}_{field_name}", "id": identifier, "state": "unknown"}
                    )
            if raw.get("schema_version") is not None and not _valid_schema_version(
                raw.get("schema_version")
            ):
                missing.append(
                    {
                        "type": f"{record_type}_schema_version",
                        "id": identifier,
                        "state": "unknown",
                    }
                )
            if (
                record_type == "span"
                and raw.get("status") is not None
                and not _known_span_status(raw.get("status"))
            ):
                missing.append(
                    {"type": "span_status", "id": identifier, "state": "unknown"}
                )
            # Values the adapter had to supply are recorded, never presented
            # as observed facts.
            if record_type == "span" and not (raw.get("kind") or raw.get("type")):
                missing.append({"type": "span_kind", "id": identifier, "state": "inferred"})
            if record_type == "event" and not (raw.get("event_type") or raw.get("type")):
                missing.append(
                    {"type": "event_event_type", "id": identifier, "state": "missing"}
                )
            if record_type == "event" and not raw.get("span_id"):
                missing.append({"type": "event_span_id", "id": identifier, "state": "inferred"})
            if not isinstance(raw.get("actor"), Mapping):
                missing.append(
                    {"type": f"{record_type}_actor", "id": identifier, "state": "inferred"}
                )
            cost = raw.get("estimated_cost_usd", raw.get("cost_usd"))
            if cost is not None and _number(cost) is None:
                missing.append(
                    {"type": f"{record_type}_cost", "id": identifier, "state": "missing"}
                )
    return missing


def _mark_external_missing(
    trace: TelemetryTrace,
    missing: list[dict[str, Any]],
) -> None:
    """Retain unknown external fields so serialization does not invent values."""

    trace_fields = {
        item["type"]
        for item in missing
        if item["type"].startswith("trace_")
    }
    if trace_fields:
        trace.metadata.extra.setdefault("external_missing_fields", [])
        trace.metadata.extra["external_missing_fields"] = sorted(
            set(trace.metadata.extra["external_missing_fields"]) | trace_fields
        )
    for prefix, records, identifier, target in (
        ("span_", trace.spans, "span_id", "attributes"),
        ("event_", trace.events, "event_id", "metadata"),
    ):
        by_record: dict[str, set[str]] = {}
        for item in missing:
            if item["type"].startswith(prefix) and item.get("id"):
                by_record.setdefault(item["id"], set()).add(item["type"].removeprefix(prefix))
        for record in records:
            names = by_record.get(getattr(record, identifier), set())
            # Unknown values serialize as null; inferred values stay (they are
            # required by the schema) and are named so no reader trusts them.
            nullable = sorted(names & _NULLABLE_EXTERNAL_FIELDS)
            inferred = sorted(names - _NULLABLE_EXTERNAL_FIELDS)
            if nullable:
                getattr(record, target)["external_missing_fields"] = nullable
            if inferred:
                getattr(record, target)["external_inferred_fields"] = inferred


_NULLABLE_EXTERNAL_FIELDS = frozenset(
    {"status", "started_at", "ended_at", "schema_version", "timestamp", "cost"}
)
# Missing-evidence names that differ from the serialized field they null.
_SERIALIZED_FIELD = {"cost": "estimated_cost_usd"}


def _valid_schema_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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
    attributes = _preserve_extra(
        attributes,
        raw,
        {
            "span_id",
            "id",
            "parent_span_id",
            "parent_id",
            "name",
            "kind",
            "type",
            "actor",
            "status",
            "started_at",
            "start_time",
            "ended_at",
            "end_time",
            "duration_ms",
            "duration",
            "input",
            "output",
            "error",
            "token_usage",
            "usage",
            "estimated_cost_usd",
            "cost_usd",
            "attributes",
            "event_ids",
            "schema_version",
            "input_capture",
            "output_capture",
        },
    )
    if raw.get("status") is not None and not _known_span_status(raw.get("status")):
        attributes["external_status"] = raw.get("status")
    return TelemetrySpan(
        trace_id=trace_id,
        span_id=str(raw.get("span_id") or raw.get("id") or "").strip(),
        parent_span_id=raw.get("parent_span_id") or raw.get("parent_id"),
        name=str(raw.get("name") or original_kind),
        kind=kind,
        actor=_map_actor(raw.get("actor"), original_kind),
        status=_map_span_status(raw.get("status")),
        started_at=raw.get("started_at") or raw.get("start_time"),
        ended_at=raw.get("ended_at") or raw.get("end_time"),
        duration_ms=_duration_ms(raw),
        input=_mapping(raw.get("input")),
        output=_mapping(raw.get("output")),
        error=_map_error(raw.get("error")),
        token_usage=_map_usage(raw.get("token_usage") or raw.get("usage")),
        estimated_cost_usd=_number(
            raw.get("estimated_cost_usd", raw.get("cost_usd"))
        ),
        attributes=attributes,
        event_ids=list(raw.get("event_ids") or []),
        schema_version=_positive_int(raw.get("schema_version"), default=1),
        input_capture=_map_capture(raw.get("input_capture")),
        output_capture=_map_capture(raw.get("output_capture")),
    )


def _map_external_event(
    raw: Mapping[str, Any], trace_id: str, root_span_id: str
) -> TelemetryEvent:
    event_type = str(raw.get("event_type") or raw.get("type") or "external_event")
    metadata = dict(raw.get("metadata") or {})
    if event_type not in FOUNDATION_EVENT_TYPES:
        metadata.setdefault("experimental", True)
        metadata.setdefault("external_event_type", event_type)
    metadata = _preserve_extra(
        metadata,
        raw,
        {
            "event_id",
            "id",
            "event_type",
            "type",
            "span_id",
            "parent_event_id",
            "parent_id",
            "actor",
            "timestamp",
            "started_at",
            "input",
            "output",
            "error",
            "duration_ms",
            "duration",
            "token_usage",
            "usage",
            "estimated_cost_usd",
            "cost_usd",
            "metadata",
            "schema_version",
            "input_capture",
            "output_capture",
        },
    )
    return TelemetryEvent(
        trace_id=trace_id,
        event_id=str(raw.get("event_id") or raw.get("id") or "").strip(),
        span_id=raw.get("span_id") or root_span_id,
        parent_event_id=raw.get("parent_event_id") or raw.get("parent_id"),
        event_type=event_type,
        actor=_map_actor(raw.get("actor"), "event"),
        timestamp=raw.get("timestamp") or raw.get("started_at"),
        input=_mapping(raw.get("input")),
        output=_mapping(raw.get("output")),
        error=_map_error(raw.get("error")),
        duration_ms=_duration_ms(raw),
        token_usage=_map_usage(raw.get("token_usage") or raw.get("usage")),
        estimated_cost_usd=_number(
            raw.get("estimated_cost_usd", raw.get("cost_usd"))
        ),
        metadata=metadata,
        schema_version=_positive_int(raw.get("schema_version"), default=1),
        input_capture=_map_capture(raw.get("input_capture")),
        output_capture=_map_capture(raw.get("output_capture")),
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
        "skipped": SpanStatus.SKIPPED,
    }.get(value, SpanStatus.RUNNING)


def _known_span_status(value: Any) -> bool:
    return str(value or "running").lower() in {
        "running",
        "success",
        "completed",
        "ok",
        "failed",
        "error",
        "cancelled",
        "timeout",
        "skipped",
    }


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
        "aborted_resource_guard": TraceStatus.ABORTED_RESOURCE_GUARD,
        "aborted_safety_guard": TraceStatus.ABORTED_SAFETY_GUARD,
        "partial": TraceStatus.PARTIAL,
    }.get(value, TraceStatus.RUNNING)


def _known_trace_status(value: Any) -> bool:
    return str(value or "running").lower() in {
        "running",
        "success",
        "completed",
        "ok",
        "failed",
        "error",
        "cancelled",
        "timeout",
        "aborted_resource_guard",
        "aborted_safety_guard",
        "partial",
    }
