from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
import json
from typing import Any

from omnicoreagent.core.telemetry.models import (
    SpanStatus,
    TelemetryError,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryStreamScope,
    TelemetryTrace,
    TraceFilter,
    TraceStatus,
    duration_ms,
    parse_datetime,
    telemetry_id,
    to_plain,
    utc_now,
)


class AbstractTelemetryStore(ABC):
    @abstractmethod
    async def append_event(self, trace_id: str, event: TelemetryEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def start_span(self, trace_id: str, span: TelemetrySpan) -> None:
        raise NotImplementedError

    @abstractmethod
    async def end_span(self, trace_id: str, span_id: str, patch: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def upsert_trace(self, trace: TelemetryTrace) -> None:
        raise NotImplementedError

    @abstractmethod
    async def update_trace(self, trace_id: str, patch: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_trace(self, trace_id: str) -> TelemetryTrace | None:
        raise NotImplementedError

    @abstractmethod
    async def list_traces(self, filter: TraceFilter | None = None) -> list[TelemetryTrace]:
        raise NotImplementedError

    @abstractmethod
    async def get_stream_cursor(self, scope: TelemetryStreamScope) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def stream_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> AsyncIterator[TelemetryEvent]:
        raise NotImplementedError

    @abstractmethod
    async def get_events_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        raise NotImplementedError


class InMemoryTelemetryStore(AbstractTelemetryStore):
    def __init__(self) -> None:
        self._traces: dict[str, TelemetryTrace] = {}
        self._trace_sequences: dict[str, int] = defaultdict(int)
        self._event_cursor = 0
        self._event_index: list[tuple[int, TelemetryEvent]] = []
        self._subscribers: dict[
            int, tuple[TelemetryStreamScope, asyncio.Queue[tuple[int, TelemetryEvent]]]
        ] = {}
        self._next_subscriber_id = 0
        self._lock = asyncio.Lock()

    async def append_event(self, trace_id: str, event: TelemetryEvent) -> None:
        if event.trace_id != trace_id:
            raise ValueError("Telemetry event trace_id does not match store trace_id")
        async with self._lock:
            trace = self._require_trace_unlocked(trace_id)
            self._trace_sequences[trace_id] += 1
            event.sequence_number = self._trace_sequences[trace_id]
            trace.events.append(_copy_event(event))
            if event.span_id:
                span = _find_span(trace, event.span_id)
                if span and event.event_id not in span.event_ids:
                    span.event_ids.append(event.event_id)
            self._index_event_unlocked(event, trace, notify=True)

    async def start_span(self, trace_id: str, span: TelemetrySpan) -> None:
        if span.trace_id != trace_id:
            raise ValueError("Telemetry span trace_id does not match store trace_id")
        async with self._lock:
            trace = self._ensure_trace_unlocked(trace_id, root_span_id=span.span_id)
            if _find_span(trace, span.span_id):
                raise ValueError(f"Span already exists: {span.span_id}")
            if span.parent_span_id and not _find_span(trace, span.parent_span_id):
                raise ValueError(f"Unknown parent span: {span.parent_span_id}")
            trace.spans.append(_copy_span(span))
            if trace.root_span_id == "":
                trace.root_span_id = span.span_id

    async def end_span(self, trace_id: str, span_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise KeyError(f"Unknown trace: {trace_id}")
            span = _find_span(trace, span_id)
            if span is None:
                raise KeyError(f"Unknown span: {span_id}")
            _patch_span(span, patch)

    async def upsert_trace(self, trace: TelemetryTrace) -> None:
        _validate_trace_identity(trace)
        async with self._lock:
            self._merge_trace_unlocked(trace)

    async def update_trace(self, trace_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise KeyError(f"Unknown trace: {trace_id}")
            _patch_trace(trace, patch)

    async def get_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            return _copy_trace(trace) if trace is not None else None

    async def list_traces(self, filter: TraceFilter | None = None) -> list[TelemetryTrace]:
        async with self._lock:
            traces = [_copy_trace(trace) for trace in self._traces.values()]
        if filter is None:
            return _sort_traces(traces)
        return sorted(
            [trace for trace in traces if filter.matches(trace)],
            key=_trace_sort_key,
        )

    async def get_stream_cursor(self, scope: TelemetryStreamScope) -> str | None:
        async with self._lock:
            return str(self._event_cursor)

    async def stream_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> AsyncIterator[TelemetryEvent]:
        queue: asyncio.Queue[tuple[int, TelemetryEvent]] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._next_subscriber_id += 1
            subscriber_id = self._next_subscriber_id
            self._subscribers[subscriber_id] = (scope, queue)
            replay = self._events_after_unlocked(scope, cursor)

        seen: set[str] = set()
        try:
            for event in replay:
                seen.add(event.event_id)
                yield event

            while True:
                _, event = await queue.get()
                if event.event_id in seen:
                    continue
                trace = self._traces.get(event.trace_id)
                if scope.matches(event, trace):
                    seen.add(event.event_id)
                    yield _copy_event(event)
        finally:
            async with self._lock:
                self._subscribers.pop(subscriber_id, None)

    async def get_events_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        async with self._lock:
            return self._events_after_unlocked(scope, cursor)

    def _events_after_unlocked(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        after = int(cursor or 0)
        events: list[TelemetryEvent] = []
        for event_cursor, event in self._event_index:
            if event_cursor <= after:
                continue
            trace = self._traces.get(event.trace_id)
            if scope.matches(event, trace):
                events.append(_copy_event(event))
        return events

    def _ensure_trace_unlocked(
        self,
        trace_id: str,
        root_span_id: str = "",
    ) -> TelemetryTrace:
        trace = self._traces.get(trace_id)
        if trace is not None:
            return trace
        if not root_span_id:
            raise KeyError(f"Unknown trace: {trace_id}")
        trace = TelemetryTrace(
            trace_id=trace_id,
            root_span_id=root_span_id,
            status=TraceStatus.RUNNING,
        )
        self._traces[trace_id] = trace
        return trace

    def _require_trace_unlocked(self, trace_id: str) -> TelemetryTrace:
        trace = self._traces.get(trace_id)
        if trace is None:
            raise KeyError(f"Unknown trace: {trace_id}")
        return trace

    def _merge_trace_unlocked(self, incoming: TelemetryTrace) -> None:
        trace_id = incoming.trace_id
        existing = self._traces.get(trace_id)
        if existing is None:
            self._traces[trace_id] = _copy_trace(incoming)
        else:
            _merge_trace(existing, incoming)
        self._rebuild_trace_event_index_unlocked(trace_id)

    def _rebuild_trace_event_index_unlocked(self, trace_id: str) -> None:
        trace = self._traces[trace_id]
        existing_event_ids = {event.event_id for _, event in self._event_index}
        for event in sorted(
            trace.events,
            key=lambda item: (item.sequence_number, item.timestamp, item.event_id),
        ):
            self._trace_sequences[trace_id] = max(
                self._trace_sequences[trace_id],
                event.sequence_number,
            )
            if event.event_id in existing_event_ids:
                continue
            self._index_event_unlocked(event, trace, notify=True)
            existing_event_ids.add(event.event_id)

    def _index_event_unlocked(
        self,
        event: TelemetryEvent,
        trace: TelemetryTrace,
        *,
        notify: bool,
    ) -> None:
        self._event_cursor += 1
        stored_event = _copy_event(event)
        self._event_index.append((self._event_cursor, stored_event))
        if not notify:
            return
        for subscriber_id, (scope, queue) in list(self._subscribers.items()):
            if not scope.matches(stored_event, trace):
                continue
            try:
                queue.put_nowait((self._event_cursor, stored_event))
            except asyncio.QueueFull:
                self._subscribers.pop(subscriber_id, None)


class JsonlTelemetryStore(AbstractTelemetryStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._inner = InMemoryTelemetryStore()
        self._loaded = False
        self._lock = asyncio.Lock()

    async def append_event(self, trace_id: str, event: TelemetryEvent) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._inner.append_event(trace_id, event)
            await self._append_record_unlocked("event", event.model_dump())

    async def start_span(self, trace_id: str, span: TelemetrySpan) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._inner.start_span(trace_id, span)
            await self._append_record_unlocked("span_start", span.model_dump())

    async def end_span(self, trace_id: str, span_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._inner.end_span(trace_id, span_id, patch)
            await self._append_record_unlocked(
                "span_end",
                {"trace_id": trace_id, "span_id": span_id, "patch": patch},
            )

    async def upsert_trace(self, trace: TelemetryTrace) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._inner.upsert_trace(trace)
            await self._append_record_unlocked("trace_upsert", trace.model_dump())

    async def update_trace(self, trace_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._inner.update_trace(trace_id, patch)
            await self._append_record_unlocked(
                "trace_update",
                {"trace_id": trace_id, "patch": patch},
            )

    async def get_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            await self._load_unlocked()
        return await self._inner.get_trace(trace_id)

    async def list_traces(self, filter: TraceFilter | None = None) -> list[TelemetryTrace]:
        async with self._lock:
            await self._load_unlocked()
        return await self._inner.list_traces(filter)

    async def get_stream_cursor(self, scope: TelemetryStreamScope) -> str | None:
        async with self._lock:
            await self._load_unlocked()
        return await self._inner.get_stream_cursor(scope)

    async def stream_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> AsyncIterator[TelemetryEvent]:
        async with self._lock:
            await self._load_unlocked()
        async for event in self._inner.stream_after(scope, cursor):
            yield event

    async def get_events_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        async with self._lock:
            await self._load_unlocked()
        return await self._inner.get_events_after(scope, cursor)

    async def _load_unlocked(self) -> None:
        if self._loaded:
            return
        if not self.path.exists():
            self._loaded = True
            return
        raw_lines = await asyncio.to_thread(self.path.read_text)
        for line in raw_lines.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                await self._replay_record_unlocked(record)
            except json.JSONDecodeError:
                continue
            except Exception:
                continue
        self._loaded = True

    async def _replay_record_unlocked(self, record: dict[str, Any]) -> None:
        record_type = record["record_type"]
        payload = record["payload"]
        if record_type == "trace_upsert":
            await self._inner.upsert_trace(TelemetryTrace.from_dict(payload))
        elif record_type == "trace_update":
            await self._inner.update_trace(payload["trace_id"], payload["patch"])
        elif record_type == "span_start":
            span = TelemetrySpan.from_dict(payload)
            await self._inner.start_span(span.trace_id, span)
        elif record_type == "span_end":
            await self._inner.end_span(
                payload["trace_id"],
                payload["span_id"],
                payload["patch"],
            )
        elif record_type == "event":
            event = TelemetryEvent.from_dict(payload)
            await self._inner.append_event(event.trace_id, event)

    async def _append_record_unlocked(
        self,
        record_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "record_id": telemetry_id("telemetry_record"),
            "record_type": record_type,
            "recorded_at": utc_now().isoformat(),
            "payload": to_plain(payload),
        }
        line = json.dumps(record, sort_keys=True) + "\n"
        await asyncio.to_thread(_append_text, self.path, line)


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _copy_event(event: TelemetryEvent) -> TelemetryEvent:
    return TelemetryEvent.from_dict(event.model_dump())


def _copy_span(span: TelemetrySpan) -> TelemetrySpan:
    return TelemetrySpan.from_dict(span.model_dump())


def _copy_trace(trace: TelemetryTrace) -> TelemetryTrace:
    return TelemetryTrace.from_dict(trace.model_dump())


def _trace_sort_key(trace: TelemetryTrace) -> tuple[Any, str]:
    return (trace.started_at, trace.trace_id)


def _sort_traces(traces: list[TelemetryTrace]) -> list[TelemetryTrace]:
    return sorted(traces, key=_trace_sort_key)


def _find_span(trace: TelemetryTrace, span_id: str) -> TelemetrySpan | None:
    return next((span for span in trace.spans if span.span_id == span_id), None)


def _patch_span(span: TelemetrySpan, patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if key == "status" and value is not None:
            value = SpanStatus(value)
        elif key == "ended_at":
            value = parse_datetime(value)
        elif key == "error" and isinstance(value, dict):
            value = TelemetryError.from_dict(value)
        setattr(span, key, value)
    if span.ended_at is None:
        span.ended_at = utc_now()
    if span.duration_ms is None:
        span.duration_ms = duration_ms(span.started_at, span.ended_at)


def _validate_trace_identity(trace: TelemetryTrace) -> None:
    for span in trace.spans:
        if span.trace_id != trace.trace_id:
            raise ValueError("Telemetry span trace_id does not match trace trace_id")
    for event in trace.events:
        if event.trace_id != trace.trace_id:
            raise ValueError("Telemetry event trace_id does not match trace trace_id")


def _patch_trace(trace: TelemetryTrace, patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if key == "status" and value is not None:
            value = TraceStatus(value)
        elif key == "ended_at":
            value = parse_datetime(value)
        setattr(trace, key, value)


def _merge_trace(existing: TelemetryTrace, incoming: TelemetryTrace) -> None:
    if _should_replace_trace_status(existing, incoming):
        existing.status = incoming.status
    if incoming.ended_at and (
        existing.ended_at is None or incoming.ended_at >= existing.ended_at
    ):
        existing.ended_at = incoming.ended_at
    existing.run_id = incoming.run_id or existing.run_id
    existing.session_id = incoming.session_id or existing.session_id
    existing.task_id = incoming.task_id or existing.task_id
    existing.suite_id = incoming.suite_id or existing.suite_id
    existing.agent_id = incoming.agent_id or existing.agent_id
    existing.workflow_id = incoming.workflow_id or existing.workflow_id
    existing.metadata = incoming.metadata or existing.metadata
    if incoming.root_span_id:
        existing.root_span_id = existing.root_span_id or incoming.root_span_id

    spans_by_id = {span.span_id: span for span in existing.spans}
    for span in incoming.spans:
        current = spans_by_id.get(span.span_id)
        if current is None or _should_replace_span(current, span):
            spans_by_id[span.span_id] = _copy_span(span)
    existing.spans = list(spans_by_id.values())

    events_by_id = {event.event_id: event for event in existing.events}
    for event in incoming.events:
        events_by_id[event.event_id] = _copy_event(event)
    existing.events = sorted(
        events_by_id.values(),
        key=lambda event: (event.sequence_number, event.timestamp, event.event_id),
    )


def _should_replace_trace_status(
    existing: TelemetryTrace,
    incoming: TelemetryTrace,
) -> bool:
    if existing.status != TraceStatus.RUNNING and incoming.status == TraceStatus.RUNNING:
        return False
    if existing.ended_at and incoming.ended_at and incoming.ended_at < existing.ended_at:
        return False
    return True


def _should_replace_span(existing: TelemetrySpan, incoming: TelemetrySpan) -> bool:
    if existing.status != SpanStatus.RUNNING and incoming.status == SpanStatus.RUNNING:
        return False
    if existing.ended_at and incoming.ended_at and incoming.ended_at < existing.ended_at:
        return False
    return True
