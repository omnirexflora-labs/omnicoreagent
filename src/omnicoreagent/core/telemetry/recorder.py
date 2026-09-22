from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import replace
import asyncio
from typing import Any

from omnicoreagent.core.continuation import mask_opaque
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
    TokenUsage,
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
    redact_sensitive_text,
)
from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.telemetry.store import AbstractTelemetryStore
from omnicoreagent.core.telemetry.exporters import (
    TelemetryExporter,
    export_trace_to_many,
)


def _span_status_for_trace_status(status: TraceStatus) -> SpanStatus:
    if status in {TraceStatus.COMPLETED, TraceStatus.SUSPENDED}:
        return SpanStatus.OK
    if status == TraceStatus.CANCELLED:
        return SpanStatus.CANCELLED
    if status == TraceStatus.TIMEOUT:
        return SpanStatus.TIMEOUT
    return SpanStatus.ERROR


def _capture_source(kind: str | None) -> str:
    normalized = str(kind or "runtime").lower()
    if normalized in {"model.call", "model_call", "model_response", "context_message", "context_tools"}:
        return "provider"
    if (
        normalized.startswith("mcp")
        or normalized.startswith("tool")
        or normalized.startswith("sandbox_exec")
    ):
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
    if normalized in {"model.call", "model_call", "context_message", "context_tools"}:
        return "model_request" if direction == "input" else "model_response"
    if normalized == "model_response":
        return "model_response"
    if normalized in {"tool.call", "mcp.tool.call", "tool_call", "mcp_tool_call"}:
        return "tool_request" if direction == "input" else "tool_result"
    if normalized in {"sandbox_exec_completed", "sandbox_exec_failed"}:
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
            error = getattr(record, "error", None)
            stack_capture = (
                error.metadata.get("stack_capture")
                if error is not None and isinstance(error.metadata, dict)
                else None
            )
            if isinstance(stack_capture, dict):
                gaps.append(
                    {
                        "type": f"{record_type}_error_stack",
                        "id": (
                            record.span_id
                            if record_type == "span"
                            else record.event_id
                        ),
                        "state": str(stack_capture.get("state")),
                    }
                )
    return gaps


def redacts_governed_arguments(recorder: Any, governed: bool) -> bool:
    """``recorder.redacts_governed_arguments``, redacting for a recorder that
    does not say (a stand-in, or none)."""
    decide = getattr(recorder, "redacts_governed_arguments", None)
    if not callable(decide):
        return governed
    return bool(decide(governed))


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
        self._trace_span_ids: dict[str, set[str]] = {}
        self._payload_trace_hint: str | None = None
        # Per trace: which messages and tool catalogs are already recorded.
        self._context_recordings: dict[str, Any] = {}

    def redacts_governed_arguments(self, governed: bool) -> bool:
        """Whether a governed agent's tool and delegation arguments are
        recorded as ``[REDACTED]``.

        Under the default capture they are: the record shows which arguments
        a call used, not their values. A capture that records model prompts
        or responses already holds those values (the model's call, and the
        next call's input), so redacting them again only loses evidence; the
        repository steward could not show which branch it pushed. There,
        arguments are recorded through the same privacy filter and secret
        keys as every other payload.
        """
        if not governed:
            return False
        return not (self.config.record_model_prompts or self.config.record_model_responses)

    def current_context(self) -> TelemetryContext | None:
        return current_telemetry_context()

    def context_recording(self) -> Any:
        """What the current trace has recorded of its model contexts."""
        from omnicoreagent.core.telemetry.context_record import ContextRecording

        context = self.current_context()
        key = context.trace_id if context is not None else ""
        return self._context_recordings.setdefault(key, ContextRecording())

    def canonicalize_for_digest(self, value: Any) -> Any:
        """Return the representation used for privacy-safe context hashes."""

        privacy_safe = self.privacy_filter.redact(mask_opaque(value), boundary="telemetry")
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
        execution_surface: str | None = None,
        provenance: TelemetryProvenance | dict[str, Any] | None = None,
        metadata: TelemetryTraceMetadata | dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
    ) -> TelemetryContext:
        trace_id = trace_id or telemetry_id("trace")
        current_parent = self.current_context()
        inherited = (
            current_parent
            if current_parent is not None and current_parent.trace_id != trace_id
            else None
        )
        if inherited is not None and parent_trace_id is None:
            parent_trace_id = inherited.trace_id
            parent_span_id = inherited.span_id
        if inherited is not None and task_id is None:
            task_id = inherited.task_id
        execution_surface = (
            execution_surface
            or (inherited.execution_surface if inherited is not None else None)
            or "interactive"
        )
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
        if inherited is not None and inherited.attempt_id is not None:
            trace.metadata.extra.setdefault(
                "background_attempt_id", inherited.attempt_id
            )
            trace.metadata.extra.setdefault(
                "background_attempt_number", inherited.attempt_number
            )
        if trace_id in self._incomplete_trace_ids:
            trace.incomplete = True
        self._trace_templates[trace_id] = trace
        self._trace_span_ids.setdefault(trace_id, set()).add(root_span.span_id)
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
            attempt_id=inherited.attempt_id if inherited is not None else None,
            attempt_number=inherited.attempt_number if inherited is not None else None,
            execution_surface=execution_surface,
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
                    await self._persist_template_end(
                        template, status=status, output=output, error=error
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
                written = await self.end_span(
                    span.span_id,
                    status=(
                        terminal_span_status
                        if span.span_id == trace.root_span_id
                        else SpanStatus.ERROR
                    ),
                    output=output if span.span_id == trace.root_span_id else None,
                    error=error if span.span_id == trace.root_span_id else None,
                )
                # Applied to the trace in hand: reading it back would copy it.
                span.status = SpanStatus(written["status"])
                span.ended_at = written["ended_at"]
                span.output = written["output"]
                span.output_capture = written["output_capture"]
                span.error = written["error"]
            # Capture gaps are computed on the final records: the root span's
            # output descriptor only exists after it has been ended above.
            final_trace = trace
            incomplete = context.trace_id in self._incomplete_trace_ids
            await self._write(
                self.store.update_trace(
                    context.trace_id,
                    {
                        "status": TraceStatus(status).value,
                        "ended_at": ended_at,
                        "incomplete": incomplete,
                        "evidence_status": (
                            TraceEvidenceStatus.PARTIAL.value
                            if incomplete or _capture_gaps(final_trace or trace)
                            else trace.evidence_status.value
                        ),
                    },
                ),
                trace_id=context.trace_id,
            )
            # A finished trace is fully on disk before its run returns.
            flush = getattr(self.store, "flush", None)
            if flush is not None:
                await self._write(flush(), trace_id=context.trace_id)
            if self.exporters:
                trace = await self._read(
                    self.store.get_trace(context.trace_id),
                    trace_id=context.trace_id,
                )
                if trace is not None:
                    if root_context is not None:
                        set_telemetry_context(root_context)
                    try:
                        from omnicoreagent.core.telemetry.context_record import (
                            with_expanded_model_inputs,
                        )

                        results = await export_trace_to_many(
                            # An exporter takes a span as it is: give it each
                            # model call's whole request.
                            with_expanded_model_inputs(trace),
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
                        # The failure stays visible in the stored trace even
                        # when strict mode propagates it to the caller.
                        await self._record_export_failure(
                            {
                                "exporter": getattr(exc, "exporter", None)
                                or "telemetry",
                                "error": str(exc),
                                "error_type": getattr(exc, "error_type", None)
                                or exc.__class__.__name__,
                            }
                        )
                        if self.config.strict:
                            raise
        finally:
            self._release_trace(context.trace_id)
            set_telemetry_context(parent_context)

    async def peek_trace(self, trace_id: str) -> TelemetryTrace | None:
        """The stored trace, uncopied, for a caller that only reads it."""
        try:
            return await self._bounded(
                self.store.peek_trace(trace_id), self.config.persistence_timeout_seconds
            )
        except Exception:
            return None

    async def read_trace(self, trace_id: str) -> TelemetryTrace | None:
        """Read a stored trace within the persistence bound; ``None`` on failure."""
        try:
            return await self._bounded(
                self.store.get_trace(trace_id), self.config.persistence_timeout_seconds
            )
        except Exception:
            return None

    async def update_trace_metadata(self, values: dict[str, Any]) -> None:
        """Merge values into the active trace's metadata.

        Used for facts known only after the trace starts, such as the tool
        catalog and system prompt versions. ``tags`` are appended.
        """
        context = self._require_context()
        template = self._trace_templates.get(context.trace_id)
        if template is None:
            return
        # The recorder started this trace and holds what it wrote; merging
        # into that costs nothing, where reading the trace back copies it.
        merged = template.metadata.model_dump()
        for key, value in values.items():
            if key == "tags":
                merged["tags"] = list(dict.fromkeys([*merged.get("tags", []), *value]))
            elif key == "extra":
                merged["extra"] = {**merged.get("extra", {}), **value}
            else:
                merged[key] = value
        recorded = self._record_metadata(merged)
        template.metadata = type(template.metadata).from_dict(recorded)
        await self._write(
            self.store.update_trace(context.trace_id, {"metadata": recorded}),
            trace_id=context.trace_id,
        )

    async def _persist_template_end(
        self,
        template: TelemetryTrace,
        *,
        status: TraceStatus | str,
        output: dict[str, Any] | None,
        error: TelemetryError | dict[str, Any] | None,
    ) -> None:
        """Persist a closed trace from local state when the store is unreadable.

        The stored copy cannot be inspected, so the result is always marked
        incomplete and partial rather than inheriting the template's defaults.
        """

        trace_status = TraceStatus(status)
        ended_at = utc_now()
        for span in template.spans:
            if span.span_id != template.root_span_id:
                continue
            recorded_output, output_capture = self._capture_output(
                output, source=span.kind
            )
            span.status = _span_status_for_trace_status(trace_status)
            span.ended_at = ended_at
            span.output = recorded_output
            span.output_capture = output_capture
            span.error = self._record_error(error)
        template.incomplete = True
        template.evidence_status = TraceEvidenceStatus.PARTIAL
        template.status = trace_status
        template.ended_at = ended_at
        await self._write(
            self.store.upsert_trace(template),
            trace_id=template.trace_id,
        )

    def _release_trace(self, trace_id: str) -> None:
        """Drop per-trace recorder state once a trace has been finalized."""

        self._trace_templates.pop(trace_id, None)
        self._context_recordings.pop(trace_id, None)
        self._incomplete_trace_ids.discard(trace_id)
        for span_id in self._trace_span_ids.pop(trace_id, set()):
            self._span_parent_contexts.pop(span_id, None)
            self._span_sources.pop(span_id, None)

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
        self._trace_span_ids.setdefault(parent.trace_id, set()).add(span.span_id)
        set_telemetry_context(context)
        return context

    async def end_span(
        self,
        span_id: str | None = None,
        *,
        status: SpanStatus | str = SpanStatus.OK,
        output: dict[str, Any] | None = None,
        error: TelemetryError | dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
        estimated_cost_usd: float | None = None,
    ) -> dict[str, Any]:
        """End a span; returns the patch written, so a caller holding the
        trace can apply it without reading the trace back."""
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
        if token_usage is not None:
            patch["token_usage"] = dict(token_usage)
        if estimated_cost_usd is not None:
            patch["estimated_cost_usd"] = estimated_cost_usd
        await self._write(
            self.store.end_span(context.trace_id, target_span_id, patch),
            trace_id=context.trace_id,
        )
        if target_span_id == context.span_id:
            set_telemetry_context(self._span_parent_contexts.get(target_span_id))
        self._span_parent_contexts.pop(target_span_id, None)
        self._span_sources.pop(target_span_id, None)
        span_ids = self._trace_span_ids.get(context.trace_id)
        if span_ids is not None:
            span_ids.discard(target_span_id)
        return patch

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
        token_usage: dict[str, Any] | None = None,
        estimated_cost_usd: float | None = None,
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
        if token_usage is not None:
            event.token_usage = TokenUsage.from_dict(token_usage)
        if estimated_cost_usd is not None:
            event.estimated_cost_usd = estimated_cost_usd
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
            # The traceback is a payload: kept only at full capture.
            error=TelemetryError.from_exception(exc, stack=self.config.capture == "full"),
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
        if (
            source in {"model.call", "model_call", "run_configuration", "context_message", "context_tools"}
            and not self.config.record_model_prompts
        ):
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
                "sandbox_exec_completed",
                "sandbox_exec_failed",
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
        if isinstance(recorded, dict) and recorded.get("not_recorded"):
            return None, TelemetryCapture(
                state=CaptureState.NOT_RECORDED,
                source=_capture_source(source),
                role=role,
                original_bytes=original_bytes,
                policy_version=self.config.fingerprint(),
                reason=str(recorded.get("reason") or "payload was not recorded"),
            )
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
        metadata = self._record_payload(record.metadata)
        if not isinstance(metadata, dict):
            metadata = {}
        stack = self._record_text(record.stack) if record.stack else None
        if stack is not None:
            encoded = stack.encode("utf-8", errors="replace")
            limit = max(self.config.max_payload_bytes, 0)
            if len(encoded) > limit:
                # The innermost frames are at the end of a Python traceback.
                stack = encoded[len(encoded) - limit :].decode(
                    "utf-8", errors="replace"
                )
                metadata["stack_capture"] = {
                    "state": CaptureState.TRUNCATED.value,
                    "original_bytes": len(encoded),
                    "recorded_bytes": len(stack.encode("utf-8")),
                }
        return TelemetryError(
            type=record.type,
            message=self._record_text(record.message),
            retryable=record.retryable,
            metadata=metadata,
            stack=stack,
        )

    def redact_text(self, value: str) -> str:
        """Apply the telemetry privacy boundary to free text before recording it."""
        return self._record_text(value)

    def _record_text(self, value: str) -> str:
        """Apply the telemetry privacy boundary to free text such as errors."""

        try:
            text = self.privacy_filter.redact_text(mask_opaque(str(value)), boundary="telemetry")
        except Exception as exc:
            # An unfiltered value must never be persisted.
            self._mark_payload_failure()
            if self.config.strict:
                raise
            return f"[not recorded: privacy redaction failed: {exc.__class__.__name__}]"
        return redact_sensitive_text(text, self.config)

    def _record_metadata(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._record_payload(value)

    def _record_payload(self, value: Any) -> Any:
        try:
            # Provider signatures and encrypted reasoning are never recorded.
            value = self.privacy_filter.redact(mask_opaque(value), boundary="telemetry")
        except Exception as exc:
            # An unfiltered value must never be persisted.
            self._mark_payload_failure()
            if self.config.strict:
                raise
            return {
                "not_recorded": True,
                "reason": f"privacy redaction failed: {exc.__class__.__name__}",
            }
        try:
            return redact_payload(
                value,
                self.config,
                payload_store=self.payload_store,
            )
        except Exception as exc:
            self._mark_payload_failure()
            if self.config.strict:
                raise
            fallback = replace(self.config, offload_large_payloads=False)
            recorded = redact_payload(value, fallback)
            if isinstance(recorded, dict) and recorded.get("truncated"):
                recorded["reason"] = (
                    f"payload offload failed: {exc.__class__.__name__}"
                )
            return recorded

    def _mark_payload_failure(self) -> None:
        """Attribute a payload failure to the trace being recorded."""

        trace_id = self._payload_trace_hint
        if trace_id is None:
            context = self.current_context()
            trace_id = context.trace_id if context is not None else None
        if trace_id is not None:
            self._incomplete_trace_ids.add(trace_id)

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
