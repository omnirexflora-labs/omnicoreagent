from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import bisect
import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
import os
from pathlib import Path
import json
import re
from typing import Any
import weakref

from omnicoreagent.core.logging import logger
from omnicoreagent.core.telemetry.models import (
    TraceEvidenceStatus,
    SpanStatus,
    TelemetryError,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryStreamScope,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TokenUsage,
    TraceFilter,
    TraceStatus,
    duration_ms,
    parse_datetime,
    telemetry_id,
    to_plain,
    utc_now,
)


class _LoopLocks:
    """One asyncio lock per running event loop.

    A store can outlive an event loop (one store object is shared per file,
    and scripts or tests call ``asyncio.run`` more than once). An
    ``asyncio.Lock`` binds to the first loop that contends for it and then
    fails in every later loop, which silently dropped best-effort writes.
    """

    def __init__(self) -> None:
        self._locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()

    def current(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = self._locks[loop] = asyncio.Lock()
        return lock


class _TelemetryStreamOverflow(RuntimeError):
    """Raised when a live telemetry subscriber cannot keep up."""


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

    async def peek_trace(self, trace_id: str) -> TelemetryTrace | None:
        """The stored trace itself, for a caller that will only read it.

        ``get_trace`` hands back a copy, so what a caller does to it cannot
        reach the store; the copy is a full walk of the trace. A reader that
        totals a finished run and changes nothing does not need one. A store
        that cannot share its object simply returns a copy.
        """
        return await self.get_trace(trace_id)

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
    @property
    def _lock(self) -> asyncio.Lock:
        return self._loop_locks.current()

    def __init__(self, *, max_traces: int | None = None) -> None:
        if max_traces is not None and max_traces < 1:
            raise ValueError("max_traces must be positive or None")
        # Finished traces beyond this bound are evicted oldest-first; running
        # traces are never evicted. ``None`` keeps every trace (used as the
        # index behind durable stores, which apply their own retention).
        self.max_traces = max_traces
        self.evicted_traces = 0
        self._traces: dict[str, TelemetryTrace] = {}
        self._trace_sequences: dict[str, int] = defaultdict(int)
        self._event_cursor = 0
        self._event_index: list[tuple[int, TelemetryEvent]] = []
        self._event_cursors: dict[str, int] = {}
        self._indexed_cursors: set[int] = set()
        self._unsorted: set[str] = set()
        self._subscribers: dict[
            int,
            tuple[
                TelemetryStreamScope,
                asyncio.Queue[tuple[int, TelemetryEvent] | _TelemetryStreamOverflow],
            ],
        ] = {}
        self._next_subscriber_id = 0
        self._loop_locks = _LoopLocks()

    async def append_event(
        self, trace_id: str, event: TelemetryEvent, *, plain: dict[str, Any] | None = None
    ) -> None:
        """Keep a copy of the event.

        ``plain`` is the event's own dump when the caller has one already (the
        durable store writes it to disk); the copy is rebuilt from it rather
        than from a second walk of the event.
        """
        if event.trace_id != trace_id:
            raise ValueError("Telemetry event trace_id does not match store trace_id")
        async with self._lock:
            trace = self._require_trace_unlocked(trace_id)
            self._trace_sequences[trace_id] += 1
            event.sequence_number = self._trace_sequences[trace_id]
            if plain is not None:
                plain["sequence_number"] = event.sequence_number
            trace_event = (
                TelemetryEvent.from_dict(plain) if plain is not None else _copy_event(event)
            )
            trace_event.stream_cursor = None
            trace.events.append(trace_event)
            if event.span_id:
                span = _find_span(trace, event.span_id)
                if span and event.event_id not in span.event_ids:
                    span.event_ids.append(event.event_id)
            self._index_event_unlocked(trace_event, trace, notify=True)

    async def start_span(
        self, trace_id: str, span: TelemetrySpan, *, plain: dict[str, Any] | None = None
    ) -> None:
        if span.trace_id != trace_id:
            raise ValueError("Telemetry span trace_id does not match store trace_id")
        async with self._lock:
            trace = self._ensure_trace_unlocked(trace_id, root_span_id=span.span_id)
            if _find_span(trace, span.span_id):
                raise ValueError(f"Span already exists: {span.span_id}")
            if span.parent_span_id and not _find_span(trace, span.parent_span_id):
                raise ValueError(f"Unknown parent span: {span.parent_span_id}")
            trace.spans.append(
                TelemetrySpan.from_dict(plain) if plain is not None else _copy_span(span)
            )
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
            self._enforce_bound_unlocked()

    async def update_trace(self, trace_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise KeyError(f"Unknown trace: {trace_id}")
            _patch_trace(trace, patch)
            self._enforce_bound_unlocked()

    def retention_status(self) -> dict[str, Any]:
        return {"max_traces": self.max_traces, "evicted": self.evicted_traces}

    def _enforce_bound_unlocked(self) -> None:
        if self.max_traces is None or len(self._traces) <= self.max_traces:
            return
        finished = sorted(
            (trace for trace in self._traces.values() if trace.ended_at is not None),
            key=lambda trace: (trace.ended_at, trace.trace_id),
        )
        excess = len(self._traces) - self.max_traces
        evicted = {trace.trace_id for trace in finished[:excess]}
        if evicted:
            self._remove_traces_unlocked(evicted)
            self.evicted_traces += len(evicted)

    async def get_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            return _copy_trace(trace) if trace is not None else None

    async def peek_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            return self._traces.get(trace_id)

    async def trace_ids_ended_before(self, cutoff: datetime) -> set[str]:
        """The finished traces older than ``cutoff``, without copying any."""
        async with self._lock:
            return {
                trace.trace_id
                for trace in self._traces.values()
                if trace.ended_at is not None and trace.ended_at < cutoff
            }

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
        queue: asyncio.Queue[
            tuple[int, TelemetryEvent] | _TelemetryStreamOverflow
        ] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._next_subscriber_id += 1
            subscriber_id = self._next_subscriber_id
            self._subscribers[subscriber_id] = (scope, queue)
            replay = self._events_after_unlocked(scope, cursor)

        # Replay and live notifications are both in cursor order, so the last
        # delivered cursor suppresses duplicates without unbounded memory.
        delivered = _parse_stream_cursor(cursor)
        try:
            for event in replay:
                delivered = max(delivered, int(event.stream_cursor))
                yield event

            while True:
                item = await queue.get()
                if isinstance(item, _TelemetryStreamOverflow):
                    raise item
                event_cursor, event = item
                if event_cursor <= delivered:
                    continue
                trace = self._traces.get(event.trace_id)
                if scope.matches(event, trace):
                    delivered = event_cursor
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

    # Persistence hooks used by durable stores that rebuild this index. They
    # restore records exactly as persisted instead of re-deriving cursors or
    # sequence numbers, so a reload cannot move a client's resume position.

    async def restore_trace(
        self,
        trace: TelemetryTrace,
        cursors: dict[str, int] | None = None,
        *,
        adopt: bool = False,
    ) -> None:
        """Put back a trace read from durable storage.

        ``adopt`` keeps the object given instead of copying it: right for a
        trace the caller has just parsed and holds no other reference to,
        which is what replaying a store does for every trace it holds.
        """
        _validate_trace_identity(trace)
        async with self._lock:
            self._merge_trace_unlocked(trace, cursors=cursors, notify=False, adopt=adopt)

    async def restore_event(
        self, event: TelemetryEvent, cursor: int | None, *, sort: bool = True
    ) -> None:
        """Put back an event read from durable storage.

        Replaying a whole store passes ``sort=False`` and calls
        ``finish_restore`` at the end, so a trace is sorted once rather than
        after every one of its events.
        """
        async with self._lock:
            trace = self._require_trace_unlocked(event.trace_id)
            if event.event_id in self._event_cursors:
                return
            self._trace_sequences[event.trace_id] = max(
                self._trace_sequences[event.trace_id], event.sequence_number
            )
            trace_event = _copy_event(event)
            trace_event.stream_cursor = None
            trace.events.append(trace_event)
            if sort:
                _sort_events(trace)
            else:
                self._unsorted.add(event.trace_id)
            if event.span_id:
                span = _find_span(trace, event.span_id)
                if span and event.event_id not in span.event_ids:
                    span.event_ids.append(event.event_id)
            self._index_event_unlocked(trace_event, trace, notify=False, cursor=cursor)

    async def finish_restore(self) -> None:
        """Sort what was restored unsorted: once per trace, after replay."""
        async with self._lock:
            for trace_id in self._unsorted:
                trace = self._traces.get(trace_id)
                if trace is not None:
                    _sort_events(trace)
            self._unsorted.clear()

    async def event_cursors(self, trace_id: str) -> dict[str, int]:
        """Return the stream cursor of every indexed event in one trace."""

        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return {}
            return {
                event.event_id: self._event_cursors[event.event_id]
                for event in trace.events
                if event.event_id in self._event_cursors
            }

    async def remove_traces(self, trace_ids: set[str]) -> None:
        """Remove traces in place, keeping subscribers and the cursor counter."""

        async with self._lock:
            self._remove_traces_unlocked(trace_ids)

    def _remove_traces_unlocked(self, trace_ids: set[str]) -> None:
        for trace_id in trace_ids:
            self._traces.pop(trace_id, None)
            self._trace_sequences.pop(trace_id, None)
        kept: list[tuple[int, TelemetryEvent]] = []
        for event_cursor, event in self._event_index:
            if event.trace_id in trace_ids:
                self._event_cursors.pop(event.event_id, None)
                self._indexed_cursors.discard(event_cursor)
            else:
                kept.append((event_cursor, event))
        self._event_index = kept

    def _events_after_unlocked(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        after = _parse_stream_cursor(cursor)
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

    def _merge_trace_unlocked(
        self,
        incoming: TelemetryTrace,
        *,
        cursors: dict[str, int] | None = None,
        notify: bool = True,
        adopt: bool = False,
    ) -> None:
        trace_id = incoming.trace_id
        existing = self._traces.get(trace_id)
        if existing is None:
            self._traces[trace_id] = incoming if adopt else _copy_trace(incoming)
        else:
            _merge_trace(existing, incoming)
        self._rebuild_trace_event_index_unlocked(
            trace_id, cursors=cursors or {}, notify=notify
        )

    def _rebuild_trace_event_index_unlocked(
        self,
        trace_id: str,
        *,
        cursors: dict[str, int],
        notify: bool,
    ) -> None:
        trace = self._traces[trace_id]
        for event in sorted(
            trace.events,
            key=lambda item: (item.sequence_number, item.timestamp, item.event_id),
        ):
            self._trace_sequences[trace_id] = max(
                self._trace_sequences[trace_id],
                event.sequence_number,
            )
            if event.event_id in self._event_cursors:
                continue
            self._index_event_unlocked(
                event,
                trace,
                notify=notify,
                cursor=cursors.get(event.event_id),
            )

    def _index_event_unlocked(
        self,
        event: TelemetryEvent,
        trace: TelemetryTrace,
        *,
        notify: bool,
        cursor: int | None = None,
    ) -> None:
        if cursor is None or cursor <= 0 or cursor in self._indexed_cursors:
            cursor = self._event_cursor + 1
        self._event_cursor = max(self._event_cursor, cursor)
        stored_event = copy.copy(event)  # the caller's copy was already isolated
        stored_event.stream_cursor = str(cursor)
        self._event_cursors[event.event_id] = cursor
        self._indexed_cursors.add(cursor)
        if not self._event_index or self._event_index[-1][0] < cursor:
            self._event_index.append((cursor, stored_event))
        else:
            bisect.insort(self._event_index, (cursor, stored_event), key=lambda item: item[0])
        if not notify:
            return
        for subscriber_id, (scope, queue) in list(self._subscribers.items()):
            if not scope.matches(stored_event, trace):
                continue
            try:
                queue.put_nowait((cursor, stored_event))
            except asyncio.QueueFull:
                self._subscribers.pop(subscriber_id, None)
                # Keep one terminal marker in the queue so a consumer blocked
                # on ``queue.get`` receives an explicit failure instead of
                # waiting forever after being evicted.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(
                    _TelemetryStreamOverflow(
                        "Telemetry stream queue overflow; reconnect from a cursor"
                    )
                )


class JsonlTelemetryStore(AbstractTelemetryStore):
    """Append-only local JSONL persistence over an in-memory index.

    Every record carries the stream cursor it was assigned, so a reload,
    a skipped corrupt line, or compaction never moves a client's resume
    position. File writes run on one dedicated worker thread: a caller that
    times out cannot cause its write to interleave with, or be overtaken by,
    the next one.
    """

    @property
    def _lock(self) -> asyncio.Lock:
        return self._loop_locks.current()

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int | None = None,
        archive: Any = None,
        compact_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        # Telemetry storage plan, T5: with an archive, a finished trace moves
        # there and leaves memory; the log keeps what is still running and is
        # compacted once it holds more than ``compact_bytes``.
        self.archive = archive
        self.compact_bytes = compact_bytes
        self._finished: set[str] = set()
        self._log_bytes = 0
        self.skipped_records = 0
        self.last_prune: dict[str, Any] | None = None
        self.removed_total = 0
        self._inner = InMemoryTelemetryStore()
        self._loaded = False
        self._loop_locks = _LoopLocks()
        self._writer: ThreadPoolExecutor | None = None
        self._handle: Any = None  # the open file, owned by the writer thread
        # Records wait here for the next turn of the loop and go to the writer
        # thread together: one hop per turn, not one per record. Measured, the
        # per-record hop was most of what a request cost.
        self._pending: list[str] = []
        self._drain: asyncio.Task | None = None
        # Stream positions this process has been given to hand out. Only a
        # shared archive issues them; alone, the counter below is enough.
        self._cursor_limit = 0

    async def append_event(self, trace_id: str, event: TelemetryEvent) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._ensure_live_unlocked(trace_id)
            await self._reserve_cursors_unlocked(1)
            # One walk of the event: the same dump is rebuilt as the in-memory
            # copy and written as the line on disk.
            payload = event.model_dump()
            await self._inner.append_event(trace_id, event, plain=dict(payload))
            payload["sequence_number"] = event.sequence_number
            payload["stream_cursor"] = str(self._inner._event_cursors[event.event_id])
            await self._append_record_unlocked("event", payload, plain=True)

    async def start_span(self, trace_id: str, span: TelemetrySpan) -> None:
        async with self._lock:
            await self._load_unlocked()
            payload = span.model_dump()
            await self._inner.start_span(trace_id, span, plain=dict(payload))
            await self._append_record_unlocked("span_start", payload, plain=True)

    async def end_span(self, trace_id: str, span_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._ensure_live_unlocked(trace_id)
            await self._inner.end_span(trace_id, span_id, patch)
            await self._append_record_unlocked(
                "span_end",
                {"trace_id": trace_id, "span_id": span_id, "patch": patch},
            )

    async def upsert_trace(self, trace: TelemetryTrace) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._ensure_live_unlocked(trace.trace_id)
            await self._reserve_cursors_unlocked(len(trace.events) or 1)
            await self._inner.upsert_trace(trace)
            self._note_finished_unlocked(trace.trace_id)
            cursors = await self._inner.event_cursors(trace.trace_id)
            await self._append_record_unlocked(
                "trace_upsert",
                trace.model_dump(),
                stream_cursors={
                    event.event_id: cursors[event.event_id]
                    for event in trace.events
                    if event.event_id in cursors
                },
                plain=True,
            )

    async def update_trace(self, trace_id: str, patch: dict[str, Any]) -> None:
        async with self._lock:
            await self._load_unlocked()
            await self._ensure_live_unlocked(trace_id)
            await self._inner.update_trace(trace_id, patch)
            self._note_finished_unlocked(trace_id)
            await self._append_record_unlocked(
                "trace_update",
                {"trace_id": trace_id, "patch": patch},
            )

    async def get_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            await self._load_unlocked()
        trace = await self._inner.get_trace(trace_id)
        if trace is None and self.archive is not None:
            archived = await self.archive.get(trace_id)
            trace = archived[0] if archived is not None else None
        return trace

    async def peek_trace(self, trace_id: str) -> TelemetryTrace | None:
        async with self._lock:
            await self._load_unlocked()
        trace = await self._inner.peek_trace(trace_id)
        if trace is None and self.archive is not None:
            archived = await self.archive.get(trace_id)
            trace = archived[0] if archived is not None else None
        return trace

    async def list_traces(self, filter: TraceFilter | None = None) -> list[TelemetryTrace]:
        async with self._lock:
            await self._load_unlocked()
        traces = await self._inner.list_traces(filter)
        if self.archive is None:
            return traces
        running = {trace.trace_id for trace in traces}
        archived = [t for t in await self.archive.list(filter) if t.trace_id not in running]
        return sorted([*traces, *archived], key=_trace_sort_key)

    async def payload_references(self) -> set[str]:
        """Every payload a kept trace refers to; archived traces through the
        index, without reading their bodies."""
        from omnicoreagent.core.telemetry.payloads import payload_references

        async with self._lock:
            await self._load_unlocked()
        references: set[str] = set()
        for trace in await self._inner.list_traces():
            references |= payload_references(trace)
        if self.archive is not None:
            references |= await self.archive.payload_references()
        return references

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
        if self.archive is not None:
            # Archived and running events together, in cursor order; then the
            # live stream from the last one delivered.
            delivered = cursor
            for event in await self.get_events_after(scope, cursor):
                delivered = event.stream_cursor
                yield event
            cursor = delivered
        async for event in self._inner.stream_after(scope, cursor):
            yield event

    async def get_events_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        async with self._lock:
            await self._load_unlocked()
        events = await self._inner.get_events_after(scope, cursor)
        if self.archive is None:
            return events
        seen = {event.event_id for event in events}
        after = _parse_stream_cursor(cursor)
        archived = [
            event
            for event, trace in await self.archive.events_after(after)
            if event.event_id not in seen and scope.matches(event, trace)
        ]
        return sorted([*events, *archived], key=lambda event: int(event.stream_cursor))

    # How many positions a process takes at a time. Small enough that two
    # processes stay roughly in step, large enough not to ask per event.
    _CURSOR_BLOCK = 32

    def _archive_issues_cursors(self) -> bool:
        index = getattr(self.archive, "index", None) if self.archive is not None else None
        return bool(index is not None and getattr(index, "shared", False))

    async def _reserve_cursors_unlocked(self, count: int) -> None:
        """Make sure this process owns the positions it is about to hand out.

        Two processes on one archive used to count from their own highest and
        give the same position to different events, so a reader resuming after
        it lost one of them. A shared index hands out blocks instead; a process
        alone keeps counting as before.
        """
        index = getattr(self.archive, "index", None) if self.archive is not None else None
        if index is None or not getattr(index, "shared", False):
            return
        needed = max(count, 1)
        if self._inner._event_cursor + needed <= self._cursor_limit:
            return
        size = max(self._CURSOR_BLOCK, needed)
        start = await asyncio.to_thread(index.reserve_cursors, size)
        self._inner._event_cursor = max(self._inner._event_cursor, start - 1)
        self._cursor_limit = start + size - 1

    async def _load_unlocked(self) -> None:
        if self._loaded:
            return
        if not self.path.exists():
            self._loaded = True
            if self.archive is not None and not self._archive_issues_cursors():
                self._inner._event_cursor = max(
                    self._inner._event_cursor, await self.archive.max_cursor()
                )
            return
        raw_lines = await asyncio.to_thread(self.path.read_text)
        self._log_bytes = len(raw_lines.encode("utf-8"))
        damaged_trace_ids: set[str] = set()
        for line in raw_lines.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                await self._replay_record_unlocked(record)
            except Exception:
                # A damaged record is evidence that was lost; count it and
                # mark its trace rather than silently dropping it.
                self.skipped_records += 1
                match = _TRACE_ID_IN_RECORD.search(line)
                if match is not None:
                    damaged_trace_ids.add(match.group(1))
        for trace_id in damaged_trace_ids:
            if trace_id in self._inner._traces:
                await self._inner.update_trace(
                    trace_id,
                    {
                        "incomplete": True,
                        "evidence_status": TraceEvidenceStatus.PARTIAL.value,
                    },
                )
        await self._inner.finish_restore()
        self._loaded = True
        if self.archive is not None:
            if not self._archive_issues_cursors():
                # Cursors keep counting from the highest ever given, archived
                # or not. A shared index issues them instead, and taking its
                # highest would walk into a block another process holds.
                self._inner._event_cursor = max(
                    self._inner._event_cursor, await self.archive.max_cursor()
                )
            # The first start of an old log is its migration: every trace in
            # it that has ended moves to the archive, and the log is
            # compacted to what is still running.
            for trace_id in list(self._inner._traces):
                self._note_finished_unlocked(trace_id)
            await self._archive_finished_unlocked()
        if self.retention_days is not None:
            await self._prune_expired_unlocked(self.retention_days, trigger="load")

    async def prune(
        self,
        retention_days: int | None = None,
        *,
        trigger: str = "explicit",
    ) -> int:
        """Remove ended traces older than the configured age and compact JSONL.

        Active traces are always retained. ``None`` disables cleanup and
        returns zero. The operation is safe to call after construction and
        returns the number of removed traces. Live subscribers stay attached
        and stream cursors never move backwards.
        """
        async with self._lock:
            await self._load_unlocked()
            days = self.retention_days if retention_days is None else retention_days
            if days is None:
                return 0
            return await self._prune_expired_unlocked(days, trigger=trigger)

    def retention_status(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "last_prune": self.last_prune,
            "removed_total": self.removed_total,
            "skipped_records": self.skipped_records,
        }

    async def _prune_expired_unlocked(self, retention_days: int, *, trigger: str) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative or None")
        cutoff = utc_now() - timedelta(days=retention_days)
        expired = await self._inner.trace_ids_ended_before(cutoff)
        if self.archive is not None:
            archived = await self.archive.ended_before(cutoff)
            if archived:
                await self.archive.remove(archived)
            expired_count_archived = len(archived)
        else:
            expired_count_archived = 0
        if expired:
            await self._inner.remove_traces(expired)
            # Only now, with something to remove, is the store read in full.
            survivors = await self._inner.list_traces()
            await self._rewrite_records_unlocked(survivors)
        removed = len(expired) + expired_count_archived
        self.last_prune = {
            "at": utc_now().isoformat(),
            "trigger": trigger,
            "retention_days": retention_days,
            "removed": removed,
        }
        self.removed_total += removed
        if expired:
            logger.info(
                "Telemetry retention removed %d trace(s) older than %d day(s) from %s",
                len(expired),
                retention_days,
                self.path,
            )
        return len(expired)

    async def _replay_record_unlocked(self, record: dict[str, Any]) -> None:
        record_type = record["record_type"]
        payload = record["payload"]
        if record_type != "span_start" and payload.get("trace_id"):
            # After compaction the log can hold a late record of a trace
            # already archived, without the records before it.
            await self._ensure_live_unlocked(payload["trace_id"])
        if record_type == "trace_upsert":
            await self._inner.restore_trace(
                TelemetryTrace.from_dict(payload),
                cursors={
                    str(event_id): int(cursor)
                    for event_id, cursor in (record.get("stream_cursors") or {}).items()
                },
                adopt=True,
            )
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
            cursor = payload.get("stream_cursor")
            event = TelemetryEvent.from_dict(payload)
            await self._inner.restore_event(
                event, int(cursor) if cursor not in (None, "") else None, sort=False
            )

    def _record_line(
        self,
        record_type: str,
        payload: dict[str, Any],
        *,
        stream_cursors: dict[str, int] | None = None,
        plain: bool = False,
    ) -> str:
        # ``trace_id`` leads the line so a record truncated by a crash can
        # still be attributed to its trace; ``payload`` is written last.
        record: dict[str, Any] = {
            "trace_id": payload.get("trace_id"),
            "record_type": record_type,
            "record_id": telemetry_id("telemetry_record"),
            "recorded_at": utc_now().isoformat(),
        }
        if stream_cursors:
            record["stream_cursors"] = stream_cursors
        # A payload that is a record's own dump is plain already; walking it
        # again would be the same walk twice. A patch may hold enums and
        # datetimes and is walked.
        record["payload"] = payload if plain else to_plain(payload)
        return json.dumps(record)

    async def _append_record_unlocked(
        self,
        record_type: str,
        payload: dict[str, Any],
        *,
        stream_cursors: dict[str, int] | None = None,
        plain: bool = False,
    ) -> None:
        line = (
            self._record_line(
                record_type, payload, stream_cursors=stream_cursors, plain=plain
            )
            + "\n"
        )
        self._pending.append(line)
        if self._drain is None or self._drain.done():
            self._drain = asyncio.get_running_loop().create_task(self._drain_pending())

    async def _drain_pending(self) -> None:
        """Send everything waiting to the writer thread, in order, in one hop.

        Started when a record arrives and runs on the next turn of the loop,
        so records written in the same turn travel together. It keeps going
        while more arrive, so there is only ever one drain, and the order on
        disk is the order recorded.
        """
        await asyncio.sleep(0)  # let the rest of this turn's records queue up
        while self._pending:
            lines, self._pending = self._pending, []
            await self._run_writer(self._append_lines, lines)

    async def flush(self) -> None:
        """Wait until every record given so far is written to the file; with
        an archive, move the traces that have ended there."""
        while self._pending or (self._drain is not None and not self._drain.done()):
            if self._drain is None or self._drain.done():
                self._drain = asyncio.get_running_loop().create_task(self._drain_pending())
            await asyncio.shield(self._drain)
        if self.archive is not None and self._finished:
            async with self._lock:
                await self._archive_finished_unlocked()

    def _note_finished_unlocked(self, trace_id: str) -> None:
        if self.archive is None:
            return
        trace = self._inner._traces.get(trace_id)
        if trace is not None and trace.ended_at is not None:
            self._finished.add(trace_id)

    async def _ensure_live_unlocked(self, trace_id: str) -> None:
        """A record for an archived trace: bring it back to apply the record;
        it is archived again at the next flush."""
        if self.archive is None or trace_id in self._inner._traces:
            return
        archived = await self.archive.get(trace_id)
        if archived is None:
            return
        trace, cursors = archived
        await self._inner.restore_trace(trace, cursors=cursors, adopt=True)
        self._finished.add(trace_id)

    async def _archive_finished_unlocked(self) -> None:
        """Move ended traces to the archive, then compact the log if it has
        grown. A trace leaves the log only after it is in the archive."""
        moved: set[str] = set()
        for trace_id in sorted(self._finished):
            trace = self._inner._traces.get(trace_id)
            if trace is None or trace.ended_at is None:
                continue
            cursors = await self._inner.event_cursors(trace_id)
            await self.archive.put(trace, cursors)
            moved.add(trace_id)
        self._finished.clear()
        if not moved:
            return
        await self._inner.remove_traces(moved)
        if self._log_bytes >= self.compact_bytes:
            await self._rewrite_records_unlocked(await self._inner.list_traces())

    def _append_lines(self, lines: list[str]) -> None:
        """Runs on the writer thread: one batch, one flush."""
        for line in lines:
            self._append_line(line)

    def _append_line(self, line: str) -> None:
        """Runs on the writer thread: the file is opened once and kept open.

        Each record is flushed to the operating system as it is written, which
        is what closing the file after every record did for durability; nothing
        called fsync then, and nothing does now.
        """
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        self._handle.write(line)
        self._handle.flush()
        self._log_bytes += len(line)

    def _close_handle(self) -> None:
        """Runs on the writer thread, before the file is replaced underneath it."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    async def _rewrite_records_unlocked(self, traces: list[TelemetryTrace]) -> None:
        records = []
        for trace in traces:
            cursors = await self._inner.event_cursors(trace.trace_id)
            records.append(
                self._record_line(
                    "trace_upsert",
                    trace.model_dump(),
                    stream_cursors=cursors,
                )
            )
        content = "" if not records else "\n".join(records) + "\n"
        while self._pending or (self._drain is not None and not self._drain.done()):
            if self._drain is None or self._drain.done():
                self._drain = asyncio.get_running_loop().create_task(self._drain_pending())
            await asyncio.shield(self._drain)
        await self._run_writer(self._close_handle)
        await self._run_writer(_replace_text, self.path, content)
        self._log_bytes = len(content.encode("utf-8"))

    async def close(self) -> None:
        """Close the file and stop the writer thread; the store stays readable."""
        await self.flush()
        if self._writer is None:
            return
        await self._run_writer(self._close_handle)
        writer, self._writer = self._writer, None
        writer.shutdown(wait=True)

    async def _run_writer(self, function, *args) -> None:
        if self._writer is None:
            self._writer = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="omnicore-telemetry-jsonl"
            )
        future = self._writer.submit(function, *args)
        # A cancelled caller must not cancel a queued write: the in-memory
        # index already holds the record, and the file must stay in order.
        await asyncio.shield(asyncio.wrap_future(future))


_TRACE_ID_IN_RECORD = re.compile(r'"trace_id":\s*"([^"]+)"')


_SHARED_JSONL_STORES: "weakref.WeakValueDictionary[Path, JsonlTelemetryStore]" = (
    weakref.WeakValueDictionary()
)


def shared_jsonl_telemetry_store(
    path: str | Path,
    *,
    retention_days: int | None = None,
    archive: bool = True,
    archive_index: Any = None,
    archive_bodies: Any = None,
) -> JsonlTelemetryStore:
    """Return the process-wide store for one JSONL file.

    Two store objects appending to the same file would keep separate indexes,
    assign conflicting stream cursors, and interleave writes, so every agent
    and manager that resolves the same path shares one object.

    Finished traces move to an archive beside the log (``<name>-archive``):
    one body per trace and an index, so memory holds only what is running.
    A deployment that shares its archive passes the index and the bodies in
    (scale plan S3); both default to the local file and directory.
    """
    resolved = Path(path).expanduser().resolve()
    store = _SHARED_JSONL_STORES.get(resolved)
    if store is None:
        from omnicoreagent.core.telemetry.archive import TelemetryArchive

        store = JsonlTelemetryStore(
            resolved,
            retention_days=retention_days,
            archive=(
                TelemetryArchive(
                    resolved.parent / f"{resolved.stem}-archive",
                    bodies=archive_bodies,
                    index=archive_index,
                )
                if archive
                else None
            ),
        )
        _SHARED_JSONL_STORES[resolved] = store
    elif store.retention_days != retention_days:
        logger.warning(
            "Telemetry store %s is already open with retention_days=%s; "
            "ignoring retention_days=%s",
            resolved,
            store.retention_days,
            retention_days,
        )
    return store


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)


def _replace_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    _write_text(temporary, text)
    os.replace(temporary, path)


def _parse_stream_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        parsed = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Telemetry stream cursor must be a non-negative integer"
        ) from exc
    if parsed < 0:
        raise ValueError("Telemetry stream cursor must be a non-negative integer")
    return parsed


def _copy_event(event: TelemetryEvent) -> TelemetryEvent:
    return TelemetryEvent.from_dict(event.model_dump())


def _sort_events(trace: TelemetryTrace) -> None:
    trace.events.sort(key=lambda item: (item.sequence_number, item.timestamp, item.event_id))


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
        elif key == "token_usage" and isinstance(value, dict):
            value = TokenUsage.from_dict(value)
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
        elif key == "evidence_status" and value is not None:
            value = TraceEvidenceStatus(value)
        elif key == "ended_at":
            value = parse_datetime(value)
        elif key == "metadata" and isinstance(value, dict):
            value = TelemetryTraceMetadata.from_dict(value)
        setattr(trace, key, value)


def _merge_trace(existing: TelemetryTrace, incoming: TelemetryTrace) -> None:
    existing.incomplete = existing.incomplete or incoming.incomplete
    if (
        existing.evidence_status == TraceEvidenceStatus.PARTIAL
        or incoming.evidence_status == TraceEvidenceStatus.PARTIAL
    ):
        existing.evidence_status = TraceEvidenceStatus.PARTIAL
    elif incoming.evidence_status != TraceEvidenceStatus.UNKNOWN:
        existing.evidence_status = incoming.evidence_status
    existing.schema_version = max(existing.schema_version, incoming.schema_version)
    existing.execution_surface = incoming.execution_surface or existing.execution_surface
    if _should_replace_trace_status(existing, incoming):
        existing.status = incoming.status
    if incoming.ended_at and (
        existing.ended_at is None or incoming.ended_at >= existing.ended_at
    ):
        existing.ended_at = incoming.ended_at
    existing.run_id = incoming.run_id or existing.run_id
    existing.session_id = incoming.session_id or existing.session_id
    existing.parent_trace_id = incoming.parent_trace_id or existing.parent_trace_id
    existing.parent_span_id = incoming.parent_span_id or existing.parent_span_id
    existing.task_id = incoming.task_id or existing.task_id
    existing.suite_id = incoming.suite_id or existing.suite_id
    existing.agent_id = incoming.agent_id or existing.agent_id
    existing.workflow_id = incoming.workflow_id or existing.workflow_id
    existing.provenance = incoming.provenance or existing.provenance
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
