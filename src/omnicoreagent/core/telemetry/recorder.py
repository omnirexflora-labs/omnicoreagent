from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from omnicoreagent.core.telemetry.context import (
    TelemetryContext,
    current_telemetry_context,
    reset_telemetry_context,
    set_telemetry_context,
)
from omnicoreagent.core.telemetry.models import (
    ActorType,
    SpanStatus,
    TelemetryActor,
    TelemetryError,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TraceStatus,
    telemetry_id,
    utc_now,
)
from omnicoreagent.core.telemetry.redaction import TelemetryConfig, redact_payload
from omnicoreagent.core.telemetry.store import AbstractTelemetryStore


def _span_status_for_trace_status(status: TraceStatus) -> SpanStatus:
    if status == TraceStatus.COMPLETED:
        return SpanStatus.OK
    if status == TraceStatus.CANCELLED:
        return SpanStatus.CANCELLED
    if status == TraceStatus.TIMEOUT:
        return SpanStatus.TIMEOUT
    return SpanStatus.ERROR


class TelemetryRecorder:
    def __init__(
        self,
        store: AbstractTelemetryStore,
        config: TelemetryConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or TelemetryConfig()
        self._span_parent_contexts: dict[str, TelemetryContext | None] = {}
        self._span_sources: dict[str, str] = {}

    def current_context(self) -> TelemetryContext | None:
        return current_telemetry_context()

    async def start_trace(
        self,
        *,
        name: str = "agent.run",
        kind: str = "agent.run",
        actor: TelemetryActor | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        suite_id: str | None = None,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        metadata: TelemetryTraceMetadata | dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
    ) -> TelemetryContext:
        trace_id = trace_id or telemetry_id("trace")
        actor = actor or TelemetryActor(type=ActorType.AGENT)
        root_span = TelemetrySpan(
            trace_id=trace_id,
            name=name,
            kind=kind,
            actor=actor,
            input=self._record_input(input, source=kind),
        )
        trace = TelemetryTrace(
            trace_id=trace_id,
            root_span_id=root_span.span_id,
            status=TraceStatus.RUNNING,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            suite_id=suite_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
            metadata=(
                TelemetryTraceMetadata.from_dict(metadata)
                if isinstance(metadata, dict)
                else metadata or TelemetryTraceMetadata()
            ),
            spans=[root_span],
        )
        await self._write(self.store.upsert_trace(trace))
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
            trace = await self._read(self.store.get_trace(context.trace_id))
            if trace is None:
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
                    {"status": TraceStatus(status).value, "ended_at": ended_at},
                )
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
        span = TelemetrySpan(
            trace_id=parent.trace_id,
            parent_span_id=parent.span_id,
            name=name,
            kind=kind,
            actor=actor or TelemetryActor(type=ActorType.SYSTEM),
            input=self._record_input(input, source=kind),
            attributes=self._record_metadata(attributes or {}),
        )
        await self._write(self.store.start_span(parent.trace_id, span))
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
        patch = {
            "status": SpanStatus(status).value,
            "ended_at": utc_now(),
            "output": self._record_output(
                output,
                source=self._span_sources.get(target_span_id),
            ),
            "error": self._record_error(error),
        }
        await self._write(self.store.end_span(context.trace_id, target_span_id, patch))
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
        event = TelemetryEvent(
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            actor=actor or TelemetryActor(type=ActorType.SYSTEM),
            input=self._record_input(input, source=event_type),
            output=self._record_output(output, source=event_type),
            error=self._record_error(error),
            duration_ms=duration_ms,
            metadata=self._record_metadata(metadata or {}),
        )
        await self._write(self.store.append_event(context.trace_id, event))
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
        if value is None or not self.config.record_inputs:
            return None
        if source in {"model.call", "model_call"} and not self.config.record_model_prompts:
            return None
        return redact_payload(value, self.config)

    def _record_output(
        self,
        value: dict[str, Any] | None,
        *,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        if value is None or not self.config.record_outputs:
            return None
        if (
            source in {"model.call", "model_response"}
            and not self.config.record_model_responses
        ):
            return None
        if (
            source in {"tool.call", "mcp.tool.call", "tool_result", "mcp_tool_result"}
            and not self.config.record_tool_results
        ):
            return None
        return redact_payload(value, self.config)

    def _record_error(
        self,
        error: TelemetryError | dict[str, Any] | None,
    ) -> TelemetryError | None:
        if error is None:
            return None
        record = TelemetryError.from_dict(error) if isinstance(error, dict) else error
        stack = redact_payload({"stack": record.stack}, self.config).get("stack")
        return TelemetryError(
            type=record.type,
            message=record.message,
            retryable=record.retryable,
            metadata=redact_payload(record.metadata, self.config),
            stack=stack,
        )

    def _record_metadata(self, value: dict[str, Any]) -> dict[str, Any]:
        return redact_payload(value, self.config)

    async def _write(self, operation) -> None:
        if self.config.strict:
            await operation
            return
        try:
            await operation
        except Exception:
            return

    async def _read(self, operation):
        if self.config.strict:
            return await operation
        try:
            return await operation
        except Exception:
            return None
