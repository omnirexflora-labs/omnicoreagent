"""An archive of finished traces: one body per trace and an index.

Telemetry storage plan, T4. A finished trace is written once, as one body:
the trace and each event's stream cursor. Bodies go through the workspace
storage interface, so a local directory, S3 or R2. A SQLite index keeps one
row per trace: what the trace is (run, parent, session, task, agent,
workflow, model, status, start and end), its first and last stream cursor,
the payloads it refers to, and its size. Listing narrows through the index
and reads only the bodies that match; a stream resumed from an old cursor
reads only the traces whose cursors are after it.

The index is behind an interface (``archive_index.py``): a SQLite file beside
the bodies by default, or any SQLAlchemy database when several processes share
one archive. The archive itself does not know which it has.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicoreagent.core.telemetry.models import (
    TelemetryEvent,
    TelemetryTrace,
    TraceFilter,
    TraceStatus,
)
from omnicoreagent.core.telemetry.archive_index import (
    FILTER_COLUMNS,
    SqliteTelemetryIndex,
    TelemetryIndex,
)
from omnicoreagent.core.telemetry.payloads import payload_references

def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class TelemetryArchive:
    """Finished traces, one body each, found through an index."""

    def __init__(
        self,
        directory: str | Path,
        *,
        bodies: Any = None,
        index: TelemetryIndex | None = None,
    ) -> None:
        # Both are opened on first use: an agent that records nothing pays
        # nothing, and nothing is imported for a database it does not have.
        self.directory = Path(directory)
        self._bodies = bodies
        self._index = index

    @property
    def bodies(self) -> Any:
        if self._bodies is None:
            from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage

            self._bodies = LocalWorkspaceStorage(self.directory / "bodies")
        return self._bodies

    @property
    def index(self) -> TelemetryIndex:
        if self._index is None:
            self._index = SqliteTelemetryIndex(self.directory)
        return self._index

    def close(self) -> None:
        if self._index is not None:
            self._index.close()

    # --- writing ----------------------------------------------------------

    async def put(self, trace: TelemetryTrace, cursors: dict[str, int]) -> None:
        """Keep a trace, replacing what was kept for it before."""
        await asyncio.to_thread(self._put, trace, dict(cursors))

    def _put(self, trace: TelemetryTrace, cursors: dict[str, int]) -> None:
        body_name = f"{trace.trace_id}.json"
        # One walk of the trace: the body written and the payload references
        # indexed beside it are read from the same plain form. A trace is the
        # largest thing the runtime keeps, so walking it twice is most of what
        # storing it would cost.
        plain = trace.model_dump()
        text = json.dumps({"trace": plain, "cursors": cursors})
        self.bodies.write_text(body_name, text)
        values = list(cursors.values())
        row = {
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "parent_trace_id": trace.parent_trace_id,
            "session_id": trace.session_id,
            "task_id": trace.task_id,
            "suite_id": trace.suite_id,
            "agent_id": trace.agent_id,
            "workflow_id": trace.workflow_id,
            "model": getattr(trace.metadata, "model", None),
            "status": _value(trace.status),
            "started_at": _iso(trace.started_at),
            "ended_at": _iso(trace.ended_at),
            "first_cursor": min(values) if values else None,
            "last_cursor": max(values) if values else None,
            "payload_references": json.dumps(sorted(payload_references(plain))),
            "body": body_name,
            "bytes": len(text.encode("utf-8")),
        }
        self.index.put(row)

    async def remove(self, trace_ids: set[str]) -> None:
        await asyncio.to_thread(self._remove, set(trace_ids))

    def _remove(self, trace_ids: set[str]) -> None:
        for body in self.index.remove(trace_ids):
            try:
                self.bodies.delete(body)
            except (FileNotFoundError, OSError, ValueError):
                pass

    # --- reading ----------------------------------------------------------

    async def contains(self, trace_id: str) -> bool:
        return await asyncio.to_thread(self.index.contains, trace_id)

    async def get(self, trace_id: str) -> tuple[TelemetryTrace, dict[str, int]] | None:
        return await asyncio.to_thread(self._get, trace_id)

    def _get(self, trace_id: str) -> tuple[TelemetryTrace, dict[str, int]] | None:
        if not self.index.contains(trace_id):
            return None
        return self._read_body(trace_id)

    def _read_body(self, trace_id: str) -> tuple[TelemetryTrace, dict[str, int]] | None:
        try:
            data = json.loads(self.bodies.read_text(f"{trace_id}.json"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        cursors = {str(k): int(v) for k, v in (data.get("cursors") or {}).items()}
        return TelemetryTrace.from_dict(data["trace"]), cursors

    async def headers(self, filter: TraceFilter | None) -> list[dict[str, Any]]:
        """Index rows of the matching traces, oldest first: no body is read."""
        return await asyncio.to_thread(self._headers, filter)

    def _headers(self, filter: TraceFilter | None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        for column in FILTER_COLUMNS:
            expected = getattr(filter, column, None) if filter is not None else None
            if expected is None:
                continue
            filters[column] = (
                TraceStatus(expected).value if column == "status" else expected
            )
        return self.index.headers(filters)

    async def list(self, filter: TraceFilter | None) -> list[TelemetryTrace]:
        """The matching traces, whole: only their bodies are read."""
        return await asyncio.to_thread(self._list, filter)

    def _list(self, filter: TraceFilter | None) -> list[TelemetryTrace]:
        traces = []
        for header in self._headers(filter):
            loaded = self._read_body(header["trace_id"])
            if loaded is not None:
                traces.append(loaded[0])
        return traces

    async def events_after(self, cursor: int) -> list[tuple[TelemetryEvent, TelemetryTrace]]:
        """Every archived event after ``cursor``, in cursor order, each with
        its trace (a stream's scope matches on the trace's run and session)."""
        return await asyncio.to_thread(self._events_after, cursor)

    def _events_after(self, cursor: int) -> list[tuple[TelemetryEvent, TelemetryTrace]]:
        found: list[tuple[int, TelemetryEvent, TelemetryTrace]] = []
        for trace_id in self.index.trace_ids_with_cursor_after(cursor):
            loaded = self._read_body(trace_id)
            if loaded is None:
                continue
            trace, cursors = loaded
            for event in trace.events:
                position = cursors.get(event.event_id)
                if position is not None and position > cursor:
                    event.stream_cursor = str(position)
                    found.append((position, event, trace))
        found.sort(key=lambda item: item[0])
        return [(event, trace) for _, event, trace in found]

    async def ended_before(self, cutoff: datetime) -> set[str]:
        return await asyncio.to_thread(self.index.trace_ids_ended_before, cutoff)

    async def max_cursor(self) -> int:
        return await asyncio.to_thread(self.index.max_cursor)

    async def payload_references(self) -> set[str]:
        return await asyncio.to_thread(self.index.payload_references)
