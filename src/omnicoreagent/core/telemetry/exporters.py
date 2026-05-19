from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any

from omnicoreagent.core.telemetry.models import (
    SpanStatus,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
)
from omnicoreagent.core.telemetry.normalizer import TelemetryNormalizer


class TelemetryExportError(RuntimeError):
    """Raised when a telemetry exporter cannot export a trace."""


@dataclass(frozen=True)
class TelemetryExportResult:
    exporter: str
    trace_id: str
    exported_spans: int = 0
    exported_events: int = 0
    destination: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "exporter": self.exporter,
            "trace_id": self.trace_id,
            "exported_spans": self.exported_spans,
            "exported_events": self.exported_events,
            "destination": self.destination,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OTelEventRecord:
    name: str
    timestamp_unix_nano: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OTelSpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    status: str
    started_at_unix_nano: int
    ended_at_unix_nano: int | None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[OTelEventRecord] = field(default_factory=list)


class TelemetryExporter(ABC):
    name: str = "telemetry"
    normalize: bool = True

    @abstractmethod
    async def export_trace(self, trace: TelemetryTrace) -> TelemetryExportResult:
        raise NotImplementedError


class InMemoryTelemetryExporter(TelemetryExporter):
    """Test and local-development exporter that keeps exported traces in memory."""

    name = "memory"

    def __init__(self, *, normalize: bool = True) -> None:
        self.normalize = normalize
        self.traces: list[TelemetryTrace] = []

    async def export_trace(self, trace: TelemetryTrace) -> TelemetryExportResult:
        export_trace = _normalize_if_requested(trace, self.normalize)
        self.traces.append(TelemetryTrace.from_dict(export_trace.model_dump()))
        return TelemetryExportResult(
            exporter=self.name,
            trace_id=export_trace.trace_id,
            exported_spans=len(export_trace.spans),
            exported_events=len(export_trace.events),
            destination="memory",
        )


class JsonlTelemetryExporter(TelemetryExporter):
    """Exporter that writes normalized traces as JSON lines."""

    name = "jsonl"

    def __init__(self, path: str, *, normalize: bool = True) -> None:
        self.path = path
        self.normalize = normalize

    async def export_trace(self, trace: TelemetryTrace) -> TelemetryExportResult:
        from pathlib import Path

        export_trace = _normalize_if_requested(trace, self.normalize)
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(export_trace.model_dump(), sort_keys=True) + "\n"
        await asyncio.to_thread(_append_text, path, line)
        return TelemetryExportResult(
            exporter=self.name,
            trace_id=export_trace.trace_id,
            exported_spans=len(export_trace.spans),
            exported_events=len(export_trace.events),
            destination=str(path),
        )


class OTelTraceMapper:
    """Pure mapper from OmniCoreAgent telemetry traces to OTEL-shaped records."""

    def map_trace(self, trace: TelemetryTrace) -> list[OTelSpanRecord]:
        events_by_span: dict[str, list[TelemetryEvent]] = {}
        trace_level_events: list[TelemetryEvent] = []
        for event in trace.events:
            if event.span_id:
                events_by_span.setdefault(event.span_id, []).append(event)
            else:
                trace_level_events.append(event)

        records: list[OTelSpanRecord] = []
        sorted_spans = sorted(trace.spans, key=lambda span: (span.started_at, span.span_id))
        span_ids = {span.span_id for span in sorted_spans}
        root_span_id = trace.root_span_id if trace.root_span_id in span_ids else None
        root_span_id = root_span_id or (sorted_spans[0].span_id if sorted_spans else None)

        if not sorted_spans and trace_level_events:
            return [self._map_trace_events_span(trace, trace_level_events)]

        for span in sorted_spans:
            span_events = list(events_by_span.get(span.span_id, []))
            if span.span_id == root_span_id:
                span_events.extend(trace_level_events)
            records.append(self._map_span(trace, span, span_events))
        return records

    def _map_trace_events_span(
        self,
        trace: TelemetryTrace,
        events: list[TelemetryEvent],
    ) -> OTelSpanRecord:
        attributes = {
            "omnicoreagent.trace_id": trace.trace_id,
            "omnicoreagent.span.kind": "runtime.control",
            "omnicoreagent.trace.status": trace.status.value,
            "omnicoreagent.synthetic_span": True,
        }
        if trace.run_id:
            attributes["omnicoreagent.run_id"] = trace.run_id
        if trace.session_id:
            attributes["omnicoreagent.session_id"] = trace.session_id
        return OTelSpanRecord(
            trace_id=trace.trace_id,
            span_id=f"span_export_{trace.trace_id}_trace_events",
            parent_span_id=None,
            name="telemetry.trace",
            kind="runtime.control",
            status=SpanStatus.ERROR.value,
            started_at_unix_nano=_to_unix_nano(trace.started_at),
            ended_at_unix_nano=(
                _to_unix_nano(trace.ended_at)
                if trace.ended_at
                else _to_unix_nano(datetime.now(timezone.utc))
            ),
            attributes={key: _to_otel_value(value) for key, value in attributes.items()},
            events=[self._map_event(event) for event in events],
        )

    def _map_span(
        self,
        trace: TelemetryTrace,
        span: TelemetrySpan,
        events: list[TelemetryEvent],
    ) -> OTelSpanRecord:
        attributes = {
            "omnicoreagent.trace_id": trace.trace_id,
            "omnicoreagent.span_id": span.span_id,
            "omnicoreagent.span.kind": span.kind,
            "omnicoreagent.trace.status": trace.status.value,
            "omnicoreagent.actor.type": span.actor.type.value,
        }
        if span.parent_span_id:
            attributes["omnicoreagent.parent_span_id"] = span.parent_span_id
        if trace.run_id:
            attributes["omnicoreagent.run_id"] = trace.run_id
        if trace.session_id:
            attributes["omnicoreagent.session_id"] = trace.session_id
        if trace.task_id:
            attributes["omnicoreagent.task_id"] = trace.task_id
        if trace.agent_id:
            attributes["omnicoreagent.agent_id"] = trace.agent_id
        if trace.workflow_id:
            attributes["omnicoreagent.workflow_id"] = trace.workflow_id
        if trace.metadata.agent_name:
            attributes["omnicoreagent.agent.name"] = trace.metadata.agent_name
        if trace.metadata.model_provider:
            attributes["gen_ai.system"] = trace.metadata.model_provider
        if trace.metadata.model:
            attributes["gen_ai.request.model"] = trace.metadata.model
        if span.actor.name:
            attributes["omnicoreagent.actor.name"] = span.actor.name
        attributes.update(_prefix_mapping("omnicoreagent.span.attribute.", span.attributes))
        attributes.update(_payload_attributes("input", span.input))
        attributes.update(_payload_attributes("output", span.output))
        if span.error:
            attributes["error.type"] = span.error.type
            attributes["error.message"] = span.error.message
        if span.token_usage.prompt_tokens is not None:
            attributes["gen_ai.usage.input_tokens"] = span.token_usage.prompt_tokens
        if span.token_usage.completion_tokens is not None:
            attributes["gen_ai.usage.output_tokens"] = span.token_usage.completion_tokens
        if span.token_usage.total_tokens is not None:
            attributes["omnicoreagent.token_usage.total"] = span.token_usage.total_tokens
        if span.estimated_cost_usd is not None:
            attributes["omnicoreagent.estimated_cost_usd"] = span.estimated_cost_usd

        return OTelSpanRecord(
            trace_id=trace.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            name=span.name,
            kind=span.kind,
            status=span.status.value,
            started_at_unix_nano=_to_unix_nano(span.started_at),
            ended_at_unix_nano=_to_unix_nano(span.ended_at) if span.ended_at else None,
            attributes={key: _to_otel_value(value) for key, value in attributes.items()},
            events=[self._map_event(event) for event in events],
        )

    def _map_event(self, event: TelemetryEvent) -> OTelEventRecord:
        attributes = {
            "omnicoreagent.event_id": event.event_id,
            "omnicoreagent.event_type": event.event_type,
            "omnicoreagent.sequence_number": event.sequence_number,
            "omnicoreagent.actor.type": event.actor.type.value,
        }
        if event.parent_event_id:
            attributes["omnicoreagent.parent_event_id"] = event.parent_event_id
        if event.actor.name:
            attributes["omnicoreagent.actor.name"] = event.actor.name
        if event.duration_ms is not None:
            attributes["omnicoreagent.duration_ms"] = event.duration_ms
        if event.error:
            attributes["error.type"] = event.error.type
            attributes["error.message"] = event.error.message
        attributes.update(_prefix_mapping("omnicoreagent.event.metadata.", event.metadata))
        attributes.update(_payload_attributes("input", event.input))
        attributes.update(_payload_attributes("output", event.output))
        return OTelEventRecord(
            name=event.event_type,
            timestamp_unix_nano=_to_unix_nano(event.timestamp),
            attributes={key: _to_otel_value(value) for key, value in attributes.items()},
        )


class OTLPHttpTelemetryExporter(TelemetryExporter):
    """Exports traces to an OTLP/HTTP traces endpoint."""

    name = "otlp"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "omnicoreagent",
        timeout: float = 10,
        normalize: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.headers = dict(headers or {})
        self.service_name = service_name
        self.timeout = timeout
        self.normalize = normalize
        self.mapper = OTelTraceMapper()

    def _resolved_endpoint(self) -> str:
        return _resolve_otlp_endpoint(self.endpoint)

    async def export_trace(self, trace: TelemetryTrace) -> TelemetryExportResult:
        try:
            return await asyncio.to_thread(self._export_sync, trace)
        except ImportError as exc:
            raise TelemetryExportError(
                "OpenTelemetry export requires the 'otel' extra: "
                'pip install "omnicoreagent[otel]"'
            ) from exc

    def _export_sync(self, trace: TelemetryTrace) -> TelemetryExportResult:
        import httpx
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        export_trace = _normalize_if_requested(trace, self.normalize)
        records = self.mapper.map_trace(export_trace)
        endpoint = self._resolved_endpoint()
        request = ExportTraceServiceRequest(
            resource_spans=[_to_resource_spans(self.service_name, self.name, records)]
        )

        headers = {
            "content-type": "application/x-protobuf",
            **self.headers,
        }
        try:
            response = httpx.post(
                endpoint,
                content=request.SerializeToString(),
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise TelemetryExportError(f"OTLP export failed: {exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise TelemetryExportError(
                "OTLP export failed with status "
                f"{response.status_code}: {response.text[:500]}"
            )
        return TelemetryExportResult(
            exporter=self.name,
            trace_id=export_trace.trace_id,
            exported_spans=len(records),
            exported_events=sum(len(record.events) for record in records),
            destination=endpoint,
        )


class LangSmithTelemetryExporter(OTLPHttpTelemetryExporter):
    """OTLP exporter preset for LangSmith."""

    name = "langsmith"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        project_name: str | None = None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "omnicoreagent",
        timeout: float = 10,
        normalize: bool = True,
    ) -> None:
        resolved_headers = dict(headers or {})
        api_key = api_key or os.getenv("LANGSMITH_API_KEY")
        project_name = (
            project_name
            or os.getenv("LANGSMITH_PROJECT")
            or os.getenv("LANGCHAIN_PROJECT")
        )
        if api_key:
            resolved_headers.setdefault("x-api-key", api_key)
        if project_name:
            resolved_headers.setdefault("Langsmith-Project", project_name)
        super().__init__(
            endpoint=(
                endpoint
                or os.getenv("LANGSMITH_OTEL_ENDPOINT")
                or "https://api.smith.langchain.com/otel/v1/traces"
            ),
            headers=resolved_headers,
            service_name=service_name,
            timeout=timeout,
            normalize=normalize,
        )


class OpikTelemetryExporter(OTLPHttpTelemetryExporter):
    """OTLP exporter preset for Comet Opik."""

    name = "opik"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        workspace: str | None = None,
        project_name: str | None = None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "omnicoreagent",
        timeout: float = 10,
        normalize: bool = True,
    ) -> None:
        resolved_headers = dict(headers or {})
        api_key = api_key or os.getenv("OPIK_API_KEY")
        workspace = workspace or os.getenv("OPIK_WORKSPACE")
        project_name = project_name or os.getenv("OPIK_PROJECT_NAME") or os.getenv(
            "OPIK_PROJECT"
        )
        if api_key:
            resolved_headers.setdefault("Authorization", api_key)
        if workspace:
            resolved_headers.setdefault("Comet-Workspace", workspace)
        if project_name:
            resolved_headers.setdefault("projectName", project_name)
        super().__init__(
            endpoint=(
                endpoint
                or os.getenv("OPIK_OTEL_ENDPOINT")
                or "https://www.comet.com/opik/api/v1/private/otel/v1/traces"
            ),
            headers=resolved_headers,
            service_name=service_name,
            timeout=timeout,
            normalize=normalize,
        )


def build_telemetry_exporter(
    destination: str,
    **kwargs: Any,
) -> TelemetryExporter:
    destination = destination.lower().strip()
    if destination == "memory":
        return InMemoryTelemetryExporter(**kwargs)
    if destination == "jsonl":
        return JsonlTelemetryExporter(**kwargs)
    if destination in {"otlp", "otel", "opentelemetry"}:
        return OTLPHttpTelemetryExporter(**kwargs)
    if destination == "langsmith":
        return LangSmithTelemetryExporter(**kwargs)
    if destination in {"opik", "comet-opik", "comet"}:
        return OpikTelemetryExporter(**kwargs)
    raise ValueError(f"Unknown telemetry exporter destination: {destination}")


async def export_trace_to_many(
    trace: TelemetryTrace,
    exporters: list[TelemetryExporter],
    *,
    strict: bool = False,
) -> list[TelemetryExportResult]:
    results: list[TelemetryExportResult] = []
    for exporter in exporters:
        try:
            results.append(await exporter.export_trace(trace))
        except Exception as exc:
            if strict:
                raise
            results.append(
                TelemetryExportResult(
                    exporter=getattr(exporter, "name", exporter.__class__.__name__),
                    trace_id=trace.trace_id,
                    destination=None,
                    metadata={"error": str(exc), "error_type": exc.__class__.__name__},
                )
            )
    return results


def _normalize_if_requested(trace: TelemetryTrace, normalize: bool) -> TelemetryTrace:
    if normalize:
        return TelemetryNormalizer().normalize(trace)
    return TelemetryTrace.from_dict(trace.model_dump())


def _to_unix_nano(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _payload_attributes(name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    return {f"omnicoreagent.{name}": _json_dumps(payload)}


def _prefix_mapping(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in payload.items()}


def _to_otel_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list) and all(isinstance(item, (str, bool, int, float)) for item in value):
        return value
    return _json_dumps(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _resolve_otlp_endpoint(endpoint: str | None) -> str:
    endpoint = (
        endpoint
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "http://localhost:4318/v1/traces"
    ).rstrip("/")
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return endpoint + "/v1/traces"


def _to_resource_spans(
    service_name: str,
    exporter_name: str,
    records: list[OTelSpanRecord],
):
    from opentelemetry.proto.common.v1.common_pb2 import InstrumentationScope
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans

    return ResourceSpans(
        resource=Resource(
            attributes=[
                _key_value("service.name", service_name),
                _key_value("omnicoreagent.exporter", exporter_name),
            ]
        ),
        scope_spans=[
            ScopeSpans(
                scope=InstrumentationScope(name="omnicoreagent.telemetry"),
                spans=[_to_otlp_span(record) for record in records],
            )
        ],
    )


def _to_otlp_span(record: OTelSpanRecord):
    from opentelemetry.proto.trace.v1.trace_pb2 import Span

    span = Span(
        trace_id=_id_bytes(record.trace_id, 16),
        span_id=_id_bytes(record.span_id, 8),
        name=record.name,
        kind=Span.SPAN_KIND_INTERNAL,
        start_time_unix_nano=record.started_at_unix_nano,
        end_time_unix_nano=(
            record.ended_at_unix_nano
            if record.ended_at_unix_nano is not None
            else _to_unix_nano(datetime.now(timezone.utc))
        ),
        attributes=[
            _key_value(key, value) for key, value in sorted(record.attributes.items())
        ],
        events=[_to_otlp_event(event) for event in record.events],
        status=_to_otlp_status(record.status),
    )
    if record.parent_span_id:
        span.parent_span_id = _id_bytes(record.parent_span_id, 8)
    return span


def _to_otlp_event(event: OTelEventRecord):
    from opentelemetry.proto.trace.v1.trace_pb2 import Span

    return Span.Event(
        name=event.name,
        time_unix_nano=event.timestamp_unix_nano,
        attributes=[
            _key_value(key, value) for key, value in sorted(event.attributes.items())
        ],
    )


def _to_otlp_status(status: str):
    from opentelemetry.proto.trace.v1.trace_pb2 import Status

    if status == SpanStatus.OK.value:
        return Status(code=Status.STATUS_CODE_OK)
    if status == SpanStatus.RUNNING.value:
        return Status(code=Status.STATUS_CODE_UNSET)
    return Status(code=Status.STATUS_CODE_ERROR, message=status)


def _key_value(key: str, value: Any):
    from opentelemetry.proto.common.v1.common_pb2 import KeyValue

    return KeyValue(key=key, value=_any_value(value))


def _any_value(value: Any):
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, ArrayValue

    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int) and not isinstance(value, bool):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    if isinstance(value, list):
        return AnyValue(
            array_value=ArrayValue(values=[_any_value(item) for item in value])
        )
    return AnyValue(string_value=str(value))


def _id_bytes(value: str, size: int) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()[:size]


def _append_text(path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
