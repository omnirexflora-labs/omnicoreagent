"""An archive of finished traces: one body per trace and an index.

Telemetry storage plan, T4. A finished trace is written once, as one body:
the trace and each event's stream cursor. Bodies go through the workspace
storage interface, so a local directory, S3 or R2. A SQLite index keeps one
row per trace: what the trace is (run, parent, session, task, agent,
workflow, model, status, start and end), its first and last stream cursor,
the payloads it refers to, and its size. Listing narrows through the index
and reads only the bodies that match; a stream resumed from an old cursor
reads only the traces whose cursors are after it.

The index is small and local. A deployment that needs one index shared by
several processes gets the same interface over Postgres.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicoreagent.core.telemetry.models import (
    TelemetryEvent,
    TelemetryTrace,
    TraceFilter,
    TraceStatus,
)
from omnicoreagent.core.telemetry.payloads import payload_references

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT,
    parent_trace_id TEXT,
    session_id TEXT,
    task_id TEXT,
    suite_id TEXT,
    agent_id TEXT,
    workflow_id TEXT,
    model TEXT,
    status TEXT,
    started_at TEXT,
    ended_at TEXT,
    first_cursor INTEGER,
    last_cursor INTEGER,
    payload_references TEXT,
    body TEXT NOT NULL,
    bytes INTEGER
);
CREATE INDEX IF NOT EXISTS traces_run ON traces (run_id);
CREATE INDEX IF NOT EXISTS traces_session ON traces (session_id);
CREATE INDEX IF NOT EXISTS traces_status ON traces (status);
CREATE INDEX IF NOT EXISTS traces_parent ON traces (parent_trace_id);
CREATE INDEX IF NOT EXISTS traces_ended ON traces (ended_at);
CREATE INDEX IF NOT EXISTS traces_last_cursor ON traces (last_cursor);
"""

_FILTER_COLUMNS = (
    "trace_id",
    "run_id",
    "session_id",
    "task_id",
    "suite_id",
    "agent_id",
    "workflow_id",
    "model",
    "status",
)
_HEADER_COLUMNS = (
    "trace_id",
    "run_id",
    "parent_trace_id",
    "session_id",
    "task_id",
    "suite_id",
    "agent_id",
    "workflow_id",
    "model",
    "status",
    "started_at",
    "ended_at",
    "first_cursor",
    "last_cursor",
    "bytes",
)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class TelemetryArchive:
    """Finished traces, one body each, found through a SQLite index."""

    def __init__(self, directory: str | Path, *, bodies: Any = None) -> None:
        from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage

        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.bodies = bodies if bodies is not None else LocalWorkspaceStorage(self.directory / "bodies")
        self._connection = sqlite3.connect(
            self.directory / "index.sqlite", check_same_thread=False, isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)
        # One connection, used from worker threads one call at a time.
        self._guard = threading.Lock()

    def close(self) -> None:
        with self._guard:
            self._connection.close()

    # --- writing ----------------------------------------------------------

    async def put(self, trace: TelemetryTrace, cursors: dict[str, int]) -> None:
        """Keep a trace, replacing what was kept for it before."""
        await asyncio.to_thread(self._put, trace, dict(cursors))

    def _put(self, trace: TelemetryTrace, cursors: dict[str, int]) -> None:
        body_name = f"{trace.trace_id}.json"
        text = json.dumps({"trace": trace.model_dump(), "cursors": cursors})
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
            "payload_references": json.dumps(sorted(payload_references(trace))),
            "body": body_name,
            "bytes": len(text.encode("utf-8")),
        }
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._guard:
            self._connection.execute(
                f"INSERT OR REPLACE INTO traces ({columns}) VALUES ({marks})",
                list(row.values()),
            )

    async def remove(self, trace_ids: set[str]) -> None:
        await asyncio.to_thread(self._remove, set(trace_ids))

    def _remove(self, trace_ids: set[str]) -> None:
        for trace_id in trace_ids:
            with self._guard:
                row = self._connection.execute(
                    "SELECT body FROM traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                self._connection.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
            if row is not None:
                try:
                    self.bodies.delete(row[0])
                except (FileNotFoundError, OSError, ValueError):
                    pass

    # --- reading ----------------------------------------------------------

    async def contains(self, trace_id: str) -> bool:
        rows = await asyncio.to_thread(
            self._query, "SELECT 1 FROM traces WHERE trace_id = ?", (trace_id,)
        )
        return bool(rows)

    async def get(self, trace_id: str) -> tuple[TelemetryTrace, dict[str, int]] | None:
        return await asyncio.to_thread(self._get, trace_id)

    def _get(self, trace_id: str) -> tuple[TelemetryTrace, dict[str, int]] | None:
        rows = self._query("SELECT 1 FROM traces WHERE trace_id = ?", (trace_id,))
        if not rows:
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
        clauses, values = [], []
        for column in _FILTER_COLUMNS:
            expected = getattr(filter, column, None) if filter is not None else None
            if expected is None:
                continue
            if column == "status":
                expected = TraceStatus(expected).value
            clauses.append(f"{column} = ?")
            values.append(expected)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT {', '.join(_HEADER_COLUMNS)} FROM traces{where} ORDER BY started_at, trace_id",
            tuple(values),
        )
        return [dict(zip(_HEADER_COLUMNS, row)) for row in rows]

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

    async def events_after(self, cursor: int) -> list[TelemetryEvent]:
        """Every archived event after ``cursor``, in cursor order."""
        return await asyncio.to_thread(self._events_after, cursor)

    def _events_after(self, cursor: int) -> list[TelemetryEvent]:
        rows = self._query(
            "SELECT trace_id FROM traces WHERE last_cursor > ? ORDER BY first_cursor", (cursor,)
        )
        found: list[tuple[int, TelemetryEvent]] = []
        for (trace_id,) in rows:
            loaded = self._read_body(trace_id)
            if loaded is None:
                continue
            trace, cursors = loaded
            for event in trace.events:
                position = cursors.get(event.event_id)
                if position is not None and position > cursor:
                    event.stream_cursor = str(position)
                    found.append((position, event))
        found.sort(key=lambda item: item[0])
        return [event for _, event in found]

    async def ended_before(self, cutoff: datetime) -> set[str]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT trace_id FROM traces WHERE ended_at IS NOT NULL AND ended_at < ?",
            (cutoff.isoformat(),),
        )
        return {row[0] for row in rows}

    async def max_cursor(self) -> int:
        rows = await asyncio.to_thread(self._query, "SELECT MAX(last_cursor) FROM traces", ())
        return int(rows[0][0] or 0) if rows else 0

    async def payload_references(self) -> set[str]:
        rows = await asyncio.to_thread(self._query, "SELECT payload_references FROM traces", ())
        references: set[str] = set()
        for (text,) in rows:
            references.update(json.loads(text or "[]"))
        return references

    def _query(self, sql: str, values: tuple) -> list[tuple]:
        with self._guard:
            return self._connection.execute(sql, values).fetchall()
