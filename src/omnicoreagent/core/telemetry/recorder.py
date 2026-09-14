from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import replace
import asyncio
from typing import Any

from omnicoreagent.core.telemetry.context import (
    TelemetryContext,
    current_telemetry_context,
    reset_telemetry_context,
    set_telemetry_context,
)
from omnicoreagent.core.telemetry.models import (
    ActorType,
    CaptureState,
    SpanStatus,
    TelemetryActor,
    TelemetryCapture,
    TelemetryError,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TelemetryProvenance,
    TraceEvidenceStatus,
    TraceStatus,
    telemetry_id,
    utc_now,
)
from omnicoreagent.core.telemetry.payloads import TelemetryPayloadStore
from omnicoreagent.core.telemetry.redaction import (
    TelemetryConfig,
    redact_payload,
    redact_sensitive_payload,
)
from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.telemetry.store import AbstractTelemetryStore
from omnicoreagent.core.telemetry.exporters import (
    TelemetryExporter,
    export_trace_to_many,
)


def _span_status_for_trace_status(status: TraceStatus) -> SpanStatus:
    if status == TraceStatus.COMPLETED:
        return SpanStatus.OK
    if status == TraceStatus.CANCELLED:
        return SpanStatus.CANCELLED
    if status == TraceStatus.TIMEOUT:
        return SpanStatus.TIMEOUT
    return SpanStatus.ERROR


def _capture_source(kind: str | None) -> str:
    normalized = str(kind or "runtime").lower()
    if normalized in {"model.call", "model_call", "model_response"}:
        return "provider"
    if normalized.startswith("mcp") or normalized.startswith("tool"):
        return "tool"
    if normalized.startswith("workspace"):
        return "workspace"
    if normalized.startswith("memory"):
        return "memory"
    if normalized in {"user_message", "request"}:
        return "user"
    if normalized.startswith("adapter"):
        return "adapter"
    return "runtime"


def _capture_role(kind: str | None, direction: str) -> str:
    normalized = str(kind or "runtime").lower()
    if normalized in {"agent.run", "request", "user_message"}:
        return "request" if direction == "input" else "final_output"
    if normalized in {"model.call", "model_call"}:
        return "model_request" if direction == "input" else "model_response"
    if normalized == "model_response":
        return "model_response"
    if normalized in {"tool.call", "mcp.tool.call", "tool_call", "mcp_tool_call"}:
        return "tool_request" if direction == "input" else "tool_result"
    if normalized in {
        "tool_result",
        "mcp_tool_result",
        "tool_error",
        "mcp_tool_error",
    }:
        return "tool_result"
    if normalized.startswith("observation") or normalized == "tool_observation":
        return "observation"
    if normalized.startswith("context"):
        return "context"
    return f"{direction}:{normalized}"


def _payload_size(value: Any) -> int | None:
    if value is None:
        return None
    try:
        import json

        return len(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return None


def _contains_redaction_marker(value: Any) -> bool:
    if value == "[REDACTED]":
        return True
    if isinstance(value, dict):
        return any(_contains_redaction_marker(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_redaction_marker(item) for item in value)
    return False


def _capture_gaps(trace: TelemetryTrace) -> list[dict[str, str]]:
    """Return payload boundaries that prevent a complete evidence claim."""
    gaps: list[dict[str, str]] = []
    unavailable = {
        CaptureState.REDACTED,
        CaptureState.TRUNCATED,
        CaptureState.NOT_RECORDED,
        CaptureState.MISSING,
        CaptureState.INFERRED,
    }
    for record_type, records in (("span", trace.spans), ("event", trace.events)):
        for record in records:
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
                            "id": (
                                record.span_id
                                if record_type == "span"
                                else record.event_id
                            ),
                            "state": state.value,
                        }
                    )
    return gaps


class TelemetryRecorder:
    def __init__(
        self,
        store: AbstractTelemetryStore,
        config: TelemetryConfig | None = None,
        exporters: list[TelemetryExporter] | None = None,
        payload_store: TelemetryPayloadStore | None = None,
        privacy_filter: PrivacyFilter | None = None,
    ) -> None:
        self.store = store
        self.config = config or TelemetryConfig()
        self.exporters = list(exporters or [])
        self.payload_store = payload_store
        self.privacy_filter = privacy_filter or PrivacyFilter()
        self._span_parent_contexts: dict[str, TelemetryContext | None] = {}
        self._span_sources: dict[str, str] = {}
        self._incomplete_trace_ids: set[str] = set()
        self._trace_templates: dict[str, TelemetryTrace] = {}
        self._pending_payload_failure = False
        self._payload_trace_hint: str | None = None

    def current_context(self) -> TelemetryContext | None:
        return current_telemetry_context()

    def canonicalize_for_digest(self, value: Any) -> Any:
        """Return the representation used for privacy-safe context hashes."""

        privacy_safe = self.privacy_filter.redact(value, boundary="telemetry")
        return redact_sensitive_payload(privacy_safe, self.config)

    async def start_trace(
        self,
        *,
        name: str = "agent.run",
        kind: str = "agent.run",
        actor: TelemetryActor | None = None,
        trace_id: str | None = None,
        parent_trace_id: str | None = None,
        parent_span_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        suite_id: str | None = None,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        execution_surface: str = "interactive",
        provenance: TelemetryProvenance | dict[str, Any] | None = None,
        metadata: TelemetryTraceMetadata | dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
    ) -> TelemetryContext:
        trace_id = trace_id or telemetry_id("trace")
        current_parent = self.current_context()
        if (
            current_parent is not None
            and current_parent.trace_id != trace_id
            and parent_trace_id is None
        ):
            parent_trace_id = current_parent.trace_id
            parent_span_id = current_parent.span_id
        actor = actor or TelemetryActor(type=ActorType.AGENT)
        previous_payload_trace_hint = self._payload_trace_hint
        self._payload_trace_hint = trace_id
        try:
            recorded_input, input_capture = self._capture_input(input, source=kind)
            root_span = TelemetrySpan(
                trace_id=trace_id,
                name=name,
                kind=kind,
                actor=actor,
                input=recorded_input,
                input_capture=input_capture,
            )
        finally:
            self._payload_trace_hint = previous_payload_trace_hint
        trace = TelemetryTrace(
            trace_id=trace_id,
            root_span_id=root_span.span_id,
            parent_trace_id=parent_trace_id,
            parent_span_id=parent_span_id,
            status=TraceStatus.RUNNING,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            suite_id=suite_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
            execution_surface=execution_surface,
            provenance=(
                TelemetryProvenance.from_dict(provenance)
                if isinstance(provenance, dict)
                else provenance or TelemetryProvenance()
            ),
            metadata=(
                TelemetryTraceMetadata.from_dict(metadata)
                if isinstance(metadata, dict)
                else metadata or TelemetryTraceMetadata()
            ),
            spans=[root_span],
        )
        if self._pending_payload_failure or trace_id in self._incomplete_trace_ids:
            trace.incomplete = True
            self._incomplete_trace_ids.add(trace_id)
            self._pending_payload_failure = False
        self._trace_templates[trace_id] = trace
        await self._write(self.store.upsert_trace(trace), trace_id=trace_id)
        context = TelemetryContext(
            trace_id=trace_id,
            span_id=root_span.span_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            suite_id=suite_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
        )
        self._span_parent_contexts[root_span.span_id] = self.current_context()
        self._span_sources[root_span.span_id] = kind
        set_telemetry_context(context)
        return context

    async def end_trace(
        self,
        *,
        status: TraceStatus | str = TraceStatus.COMPLETED,
        output: dict[str, Any] | None = None,
        error: TelemetryError | dict[str, Any] | None = None,
    ) -> None:
        context = self._require_context()
        root_context: TelemetryContext | None = None
        parent_context: TelemetryContext | None = self._root_parent_context(context)
        try:
            trace = await self._read(
                self.store.get_trace(context.trace_id),
                trace_id=context.trace_id,
            )
            if trace is None:
                template = self._trace_templates.get(context.trace_id)
                if template is not None:
                    template.incomplete = True
                    template.status = TraceStatus(status)
                    template.ended_at = utc_now()
                    await self._write(
                        self.store.upsert_trace(template),
                        trace_id=context.trace_id,
                    )
                return
            root_context = TelemetryContext(
                trace_id=trace.trace_id,
                span_id=trace.root_span_id,
                run_id=trace.run_id,
                session_id=trace.session_id,
                task_id=trace.task_id,
                suite_id=trace.suite_id,
                agent_id=trace.agent_id,
                workflow_id=trace.workflow_id,
            )
            parent_context = self._span_parent_contexts.get(trace.root_span_id)
            ended_at = utc_now()
            trace_status = TraceStatus(status)
            terminal_span_status = _span_status_for_trace_status(trace_status)
            for span in sorted(
                trace.spans,
                key=lambda item: item.started_at,
                reverse=True,
            ):
                if span.status != SpanStatus.RUNNING:
                    continue
                set_telemetry_context(
                    root_context.child(span.span_id)
                    if root_context is not None
                    else context.child(span.span_id)
                )
                await self.end_span(
                    span.span_id,
                    status=(
                        terminal_span_status
                        if span.span_id == trace.root_span_id
                        else SpanStatus.ERROR
                    ),
                    output=output if span.span_id == trace.root_span_id else None,
                    error=error if span.span_id == trace.root_span_id else None,
                )
            await self._write(
                self.store.update_trace(
                    context.trace_id,
                    {
                        "status": TraceStatus(status).value,
                        "ended_at": ended_at,
                        "incomplete": context.trace_id in self._incomplete_trace_ids,
                        "evidence_status": (
                            TraceEvidenceStatus.PARTIAL.value
                            if context.trace_id in self._incomplete_trace_ids
                            or _capture_gaps(trace)
                            else trace.evidence_status.value
                        ),
                    },
                ),
                trace_id=context.trace_id,
            )
            if self.exporters:
                trace = await self._read(
                    self.store.get_trace(context.trace_id),
                    trace_id=context.trace_id,
                )
                if trace is not None:
                    if root_context is not None:
                        set_telemetry_context(root_context)
                    try:
                        results = await export_trace_to_many(
                            trace,
                            self.exporters,
                            strict=self.config.strict,
                            timeout=self.config.export_timeout_seconds,
                        )
                        failures = [
                            result
                            for result in results
                            if "error" in result.metadata
                        ]
                        for failure in failures:
                            await self._record_export_failure(failure)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if self.config.strict:
                            raise
                        await self._record_export_failure(
                            {
                                "exporter": "telemetry",
                                "error": str(exc),
                                "error_type": exc.__class__.__name__,
                            }
                        )
        finally:
            set_telemetry_context(parent_context)

    def _root_parent_context(self, context: TelemetryContext) -> TelemetryContext | None:
        current_span_id = context.span_id
        seen: set[str] = set()
        while current_span_id and current_span_id not in seen:
            seen.add(current_span_id)
            parent = self._span_parent_contexts.get(current_span_id)
            if parent is None:
                return None
            if parent.trace_id != context.trace_id:
                return parent
            current_span_id = parent.span_id
        return None

    async def start_span(
        self,
        *,
        name: str,
        kind: str,
        actor: TelemetryActor | None = None,
        input: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TelemetryContext:
        parent = self._require_context()
        recorded_input, input_capture = self._capture_input(input, source=kind)
        span = TelemetrySpan(
            trace_id=parent.trace_id,
            parent_span_id=parent.span_id,
            name=name,
            kind=kind,
            actor=actor or TelemetryActor(type=ActorType.SYSTEM),
            input=recorded_input,
            input_capture=input_capture,
            attributes=self._record_metadata(attributes or {}),
        )
        await self._write(
            self.store.start_span(parent.trace_id, span),
            trace_id=parent.trace_id,
        )
        context = parent.child(span.span_id)
        self._span_parent_contexts[span.span_id] = parent
        self._span_sources[span.span_id] = kind
        set_telemetry_context(context)
        return context

    async def end_span(
        self,
        span_id: str | None = None,
        *,
        status: SpanStatus | str = SpanStatus.OK,
        output: dict[str, Any] | None = None,
        error: TelemetryError | dict[str, Any] | None = None,
    ) -> None:
        context = self._require_context()
        target_span_id = span_id or context.span_id
        if target_span_id is None:
            raise RuntimeError("No active telemetry span")
        recorded_output, output_capture = self._capture_output(
            output,
            source=self._span_sources.get(target_span_id),
        )
        patch = {
            "status": SpanStatus(status).value,
            "ended_at": utc_now(),
            "output": recorded_output,
            "output_capture": output_capture,
            "error": self._record_error(error),
        }
        await self._write(
            self.store.end_span(context.trace_id, target_span_id, patch),
            trace_id=context.trace_id,
        )
        if target_span_id == context.span_id:
            set_telemetry_context(self._span_parent_contexts.get(target_span_id))
        self._span_parent_contexts.pop(target_span_id, None)
        self._span_sources.pop(target_span_id, None)

    @asynccontextmanager
    async def span(
        self,
        *,
        name: str,
        kind: str,
        actor: TelemetryActor | None = None,
        input: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> AsyncIterator[TelemetryContext]:
        previous = self.current_context()
        token = set_telemetry_context(previous)
        context = await self.start_span(
            name=name,
            kind=kind,
            actor=actor,
            input=input,
            attributes=attributes,
        )
        try:
            yield context
        except Exception as exc:
            await self.end_span(
                context.span_id,
                status=SpanStatus.ERROR,
                error=TelemetryError.from_exception(exc),
            )
            raise
        else:
            await self.end_span(context.span_id, status=SpanStatus.OK)
        finally:
            reset_telemetry_context(token)

    async def emit_event(
        self,
        event_type: str,
        *,
        actor: TelemetryActor | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        error: TelemetryError | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        parent_event_id: str | None = None,
    ) -> TelemetryEvent:
        context = self._require_context()
        recorded_input, input_capture = self._capture_input(input, source=event_type)
        recorded_output, output_capture = self._capture_output(output, source=event_type)
        event_metadata = dict(metadata or {})
        correlation_metadata: dict[str, Any] = {}
        for key in (
            "run_id",
            "session_id",
            "task_id",
            "suite_id",
            "agent_id",
            "workflow_id",
        ):
            value = getattr(context, key)
            if value is not None:
                correlation_metadata[key] = value
                event_metadata.setdefault(key, value)
        recorded_metadata = self._record_metadata(event_metadata)
        if not isinstance(recorded_metadata, dict):
            recorded_metadata = {}
        recorded_metadata.update(correlation_metadata)
        event = TelemetryEvent(
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            actor=actor or TelemetryActor(type=ActorType.SYSTEM),
            input=recorded_input,
            output=recorded_output,
            error=self._record_error(error),
            duration_ms=duration_ms,
            metadata=recorded_metadata,
            input_capture=input_capture,
            output_capture=output_capture,
        )
        await self._write(
            self.store.append_event(context.trace_id, event),
            trace_id=context.trace_id,
        )
        return event

    async def record_exception(
        self,
        exc: BaseException,
        *,
        event_type: str = "runtime_error",
        actor: TelemetryActor | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryEvent:
        return await self.emit_event(
            event_type,
            actor=actor,
            error=TelemetryError.from_exception(exc),
            metadata=metadata,
        )

    def adopt_context(self, context: TelemetryContext | None):
        return set_telemetry_context(context)

    def child_context(self, span_id: str) -> TelemetryContext:
        context = self._require_context()
        return replace(context, span_id=span_id)

    def _require_context(self) -> TelemetryContext:
        context = self.current_context()
        if context is None:
            raise RuntimeError("No active telemetry context")
        return context

    def _record_input(
        self,
        value: dict[str, Any] | None,
        *,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        return self._capture_input(value, source=source)[0]

    def _record_output(
        self,
        value: dict[str, Any] | None,
        *,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        return self._capture_output(value, source=source)[0]

    def _capture_input(
        self,
        value: dict[str, Any] | None,
        *,
        source: str | None = None,
    ) -> tuple[dict[str, Any] | None, TelemetryCapture | None]:
        if value is None:
            return None, None
        role = _capture_role(source, "input")
        descriptor_source = _capture_source(source)
        if not self.config.record_inputs:
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=descriptor_source,
                role=role,
                policy_version=self.config.fingerprint(),
                reason="capture disabled by telemetry policy",
            )
        if source in {"model.call", "model_call"} and not self.config.record_model_prompts:
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=descriptor_source,
                role=role,
                policy_version=self.config.fingerprint(),
                reason="model prompt capture disabled by telemetry policy",
            )
        return self._capture_recorded_payload(value, source=source, role=role)

    def _capture_output(
        self,
        value: dict[str, Any] | None,
        *,
        source: str | None = None,
    ) -> tuple[dict[str, Any] | None, TelemetryCapture | None]:
        if value is None:
            return None, None
        role = _capture_role(source, "output")
        descriptor_source = _capture_source(source)
        if not self.config.record_outputs:
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=descriptor_source,
                role=role,
                policy_version=self.config.fingerprint(),
                reason="capture disabled by telemetry policy",
            )
        if source == "model_response" and not self.config.record_model_responses:
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=descriptor_source,
                role=role,
                policy_version=self.config.fingerprint(),
                reason="model response capture disabled by telemetry policy",
            )
        if (
            source
            in {
                "tool.call",
                "mcp.tool.call",
                "workspace.read",
                "workspace.write",
                "workspace.delete",
                "observation.pipeline",
                "tool.batch",
                "tool_result",
                "mcp_tool_result",
                "tool_observation",
                "workspace_read",
                "workspace_write",
                "workspace_delete",
                "observation_pipeline_end",
            }
            and not self.config.record_tool_results
        ):
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=descriptor_source,
                role=role,
                policy_version=self.config.fingerprint(),
                reason="tool result capture disabled by telemetry policy",
            )
        return self._capture_recorded_payload(value, source=source, role=role)

    def _capture_recorded_payload(
        self,
        value: dict[str, Any],
        *,
        source: str | None,
        role: str,
    ) -> tuple[dict[str, Any], TelemetryCapture]:
        recorded = self._record_payload(value)
        original_bytes = _payload_size(value)
        recorded_bytes = _payload_size(recorded)
        state = CaptureState.AVAILABLE
        reference = None
        reason = None
        if isinstance(recorded, dict) and recorded.get("offloaded"):
            state = CaptureState.OFFLOADED
            reference = recorded.get("reference")
        elif isinstance(recorded, dict) and recorded.get("truncated"):
            state = CaptureState.TRUNCATED
            reason = str(recorded.get("reason") or "payload exceeded telemetry limit")
        elif _contains_redaction_marker(recorded) or recorded != value:
            state = CaptureState.REDACTED
        return recorded, TelemetryCapture(
            state=state,
            source=_capture_source(source),
            role=role,
            reference=reference,
            content_type="application/json",
            checksum=(recorded.get("checksum") if isinstance(recorded, dict) else None),
            original_bytes=(
                recorded.get("original_bytes", original_bytes)
                if isinstance(recorded, dict)
                else original_bytes
            ),
            recorded_bytes=recorded_bytes,
            policy_version=self.config.fingerprint(),
            reason=reason,
        )

    def _record_error(
        self,
        error: TelemetryError | dict[str, Any] | None,
    ) -> TelemetryError | None:
        if error is None:
            return None
        record = TelemetryError.from_dict(error) if isinstance(error, dict) else error
        stack = self._record_payload({"stack": record.stack}).get("stack")
        return TelemetryError(
            type=record.type,
            message=record.message,
            retryable=record.retryable,
            metadata=self._record_payload(record.metadata),
            stack=stack,
        )

    def _record_metadata(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._record_payload(value)

    def _record_payload(self, value: Any) -> Any:
        try:
            value = self.privacy_filter.redact(value, boundary="telemetry")
            return redact_payload(
                value,
                self.config,
                payload_store=self.payload_store,
            )
        except Exception:
            trace_id = self._payload_trace_hint
            if trace_id is None:
                context = self.current_context()
                trace_id = context.trace_id if context is not None else None
            if trace_id is None:
                self._pending_payload_failure = True
            else:
                self._incomplete_trace_ids.add(trace_id)
            if self.config.strict:
                raise
            fallback = replace(self.config, offload_large_payloads=False)
            return redact_payload(value, fallback)

    async def _write(self, operation, *, trace_id: str | None = None) -> None:
        try:
            await self._bounded(operation, self.config.persistence_timeout_seconds)
        except Exception:
            if trace_id is not None:
                self._incomplete_trace_ids.add(trace_id)
            if self.config.strict:
                raise

    async def _read(self, operation, *, trace_id: str | None = None):
        try:
            return await self._bounded(operation, self.config.persistence_timeout_seconds)
        except Exception:
            if trace_id is not None:
                self._incomplete_trace_ids.add(trace_id)
            if self.config.strict:
                raise
            return None

    async def _bounded(self, operation, timeout: float | None):
        if timeout is None:
            return await operation
        return await asyncio.wait_for(operation, timeout=timeout)

    async def _record_export_failure(self, failure: Any) -> None:
        """Keep optional exporter failures visible without changing run status."""

        exporter = (
            failure.get("exporter", "telemetry")
            if isinstance(failure, dict)
            else getattr(failure, "exporter", "telemetry")
        )
        error = (
            failure
            if isinstance(failure, dict)
            else failure.model_dump()
            if hasattr(failure, "model_dump")
            else {"error": str(failure)}
        )
        await self.emit_event(
            "telemetry_error",
            metadata={"component": "exporter", "exporter": str(exporter)},
            error={
                "type": str(error.get("error_type", "TelemetryExportError")),
                "message": str(error.get("error", "telemetry export failed")),
                "metadata": {"exporter": str(exporter)},
            },
        )
