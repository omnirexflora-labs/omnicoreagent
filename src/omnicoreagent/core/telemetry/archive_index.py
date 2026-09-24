"""The archive's index: one row per finished trace, local or shared.

Scale plan, S3. The archive keeps one body per trace through the workspace
storage interface — a directory, S3, R2 — and finds them through this index.
It was a SQLite file in the archive's own directory, which means two server
processes can share the bodies and not the index that finds them.

So the index is an interface, and the questions it answers are the ones the
archive asks:

    keep this row; drop these rows; do you hold this trace; the headers
    matching this filter, oldest first; the traces with a cursor after this
    one; the traces that ended before then; the highest cursor you hold; the
    payloads your traces refer to.

``SqliteTelemetryIndex`` is what the archive has always used and still the
default: a file beside the bodies, no dependency, opened on first use.
``SqlTelemetryIndex`` is the same index over SQLAlchemy Core, so several
processes can share one PostgreSQL (or MySQL, or one SQLite file) and each
find what the others archived.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

# What a row holds: what the trace is, where its body is, the stream cursors
# it spans, the payloads it refers to, and its size.
COLUMNS = (
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
    "payload_references",
    "body",
    "bytes",
)
FILTER_COLUMNS = (
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
HEADER_COLUMNS = (
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


class TelemetryIndex(ABC):
    """Where finished traces are looked up. Called from worker threads."""

    #: Whether more than one process can be keeping traces here. A shared
    #: index also hands out stream cursors, because two processes counting
    #: their own would give the same position to different events.
    shared: bool = False

    def reserve_cursors(self, count: int) -> int:
        """Reserve ``count`` stream positions and return the first.

        Only a shared index needs this: one process counting alone never
        collides with itself.
        """
        raise NotImplementedError

    @abstractmethod
    def put(self, row: dict[str, Any]) -> None:
        """Keep this row, replacing whatever was kept for its trace."""

    @abstractmethod
    def remove(self, trace_ids: set[str]) -> list[str]:
        """Drop these traces; return the body names they pointed at."""

    @abstractmethod
    def contains(self, trace_id: str) -> bool: ...

    @abstractmethod
    def headers(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """The rows matching every filter, oldest first."""

    @abstractmethod
    def trace_ids_with_cursor_after(self, cursor: int) -> list[str]:
        """The traces holding an event after ``cursor``, in cursor order."""

    @abstractmethod
    def trace_ids_ended_before(self, cutoff: datetime) -> set[str]: ...

    @abstractmethod
    def max_cursor(self) -> int: ...

    @abstractmethod
    def payload_references(self) -> set[str]: ...

    @abstractmethod
    def close(self) -> None: ...

    def drop(self) -> None:  # pragma: no cover - tests and teardown
        """Remove the index's own storage. Only tests need this."""
        raise NotImplementedError


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


class SqliteTelemetryIndex(TelemetryIndex):
    """A file beside the bodies: the default, and what one process needs."""

    def __init__(self, directory: str | Path) -> None:
        # Opened on first use: an agent that records nothing pays nothing.
        self.directory = Path(directory)
        self._connection: sqlite3.Connection | None = None
        # One connection, used from worker threads one call at a time.
        self._guard = threading.Lock()

    def _open(self) -> sqlite3.Connection:
        """Call with the guard held."""
        if self._connection is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.directory / "index.sqlite",
                check_same_thread=False,
                isolation_level=None,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            self._connection = connection
        return self._connection

    def _query(self, sql: str, values: tuple = ()) -> list[tuple]:
        with self._guard:
            return self._open().execute(sql, values).fetchall()

    def put(self, row: dict[str, Any]) -> None:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._guard:
            self._open().execute(
                f"INSERT OR REPLACE INTO traces ({columns}) VALUES ({marks})",
                list(row.values()),
            )

    def remove(self, trace_ids: set[str]) -> list[str]:
        bodies: list[str] = []
        for trace_id in trace_ids:
            with self._guard:
                connection = self._open()
                row = connection.execute(
                    "SELECT body FROM traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                connection.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
            if row is not None:
                bodies.append(row[0])
        return bodies

    def contains(self, trace_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM traces WHERE trace_id = ?", (trace_id,)))

    def headers(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clauses = [f"{column} = ?" for column in filters]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT {', '.join(HEADER_COLUMNS)} FROM traces{where} "
            "ORDER BY started_at, trace_id",
            tuple(filters.values()),
        )
        return [dict(zip(HEADER_COLUMNS, row)) for row in rows]

    def trace_ids_with_cursor_after(self, cursor: int) -> list[str]:
        return [
            row[0]
            for row in self._query(
                "SELECT trace_id FROM traces WHERE last_cursor > ? ORDER BY first_cursor",
                (cursor,),
            )
        ]

    def trace_ids_ended_before(self, cutoff: datetime) -> set[str]:
        return {
            row[0]
            for row in self._query(
                "SELECT trace_id FROM traces WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff.isoformat(),),
            )
        }

    def max_cursor(self) -> int:
        rows = self._query("SELECT MAX(last_cursor) FROM traces")
        return int(rows[0][0] or 0) if rows else 0

    def payload_references(self) -> set[str]:
        references: set[str] = set()
        for (text,) in self._query("SELECT payload_references FROM traces"):
            references.update(json.loads(text or "[]"))
        return references

    def close(self) -> None:
        with self._guard:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def drop(self) -> None:
        self.close()
        path = self.directory / "index.sqlite"
        if path.exists():
            path.unlink()


class SqlTelemetryIndex(TelemetryIndex):
    """The same index in any SQLAlchemy database, so processes can share it.

    A trace's row is written once, when the trace is archived, and read back by
    whichever process is asked for it. Timestamps stay the ISO strings the
    SQLite index used, so a row means the same thing in either implementation
    and ordering is the same lexicographic ordering.
    """

    shared = True

    def __init__(self, url: str, *, table_prefix: str = "") -> None:
        self.url = url
        self.table_prefix = table_prefix
        self._engine: Any = None
        self._table: Any = None
        self._guard = threading.Lock()

    def _open(self):
        """Call with the guard held."""
        if self._engine is not None:
            return self._engine, self._table
        from sqlalchemy import (
            Column,
            Index,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            create_engine,
        )

        kwargs: dict[str, Any] = {"future": True}
        if self.url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        engine = create_engine(self.url, **kwargs)
        metadata = MetaData()
        name = f"{self.table_prefix}telemetry_traces"
        table = Table(
            name,
            metadata,
            Column("trace_id", String(255), primary_key=True),
            *(
                Column(column, String(255))
                for column in (
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
                )
            ),
            Column("first_cursor", Integer),
            Column("last_cursor", Integer),
            Column("payload_references", Text),
            Column("body", String(512), nullable=False),
            Column("bytes", Integer),
            # What the archive asks for: a run's or session's traces, a
            # stream's position, and what has aged out.
            Index(f"{name}_run", "run_id"),
            Index(f"{name}_session", "session_id"),
            Index(f"{name}_status", "status"),
            Index(f"{name}_parent", "parent_trace_id"),
            Index(f"{name}_ended", "ended_at"),
            Index(f"{name}_cursor", "last_cursor"),
        )
        # One row, holding the next stream position no process has taken.
        cursors = Table(
            f"{self.table_prefix}telemetry_cursor_sequence",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("next_cursor", Integer, nullable=False),
        )
        metadata.create_all(engine)
        self._engine, self._table, self._metadata = engine, table, metadata
        self._cursors = cursors
        return engine, table

    def reserve_cursors(self, count: int) -> int:
        """Take ``count`` positions from the shared sequence, atomically.

        The update names the value it read, so two processes asking at once
        cannot be given the same block: the loser sees no row updated and asks
        again.
        """
        from sqlalchemy import func, select

        with self._guard:
            engine, table = self._open()
            cursors = self._cursors
        highest = 0
        for _ in range(50):
            with engine.begin() as connection:
                row = connection.execute(
                    cursors.select().where(cursors.c.id == 1)
                ).first()
                if row is None:
                    # Start above anything already archived, so an upgrading
                    # deployment does not reissue positions it has used.
                    highest = int(
                        connection.execute(
                            select(func.max(table.c.last_cursor))
                        ).scalar()
                        or 0
                    )
                    try:
                        connection.execute(
                            cursors.insert().values(id=1, next_cursor=highest + 1 + count)
                        )
                    except Exception:
                        continue  # another process created it first
                    return highest + 1
                taken = int(row.next_cursor)
                updated = connection.execute(
                    cursors.update()
                    .where(cursors.c.id == 1, cursors.c.next_cursor == taken)
                    .values(next_cursor=taken + count)
                )
                if updated.rowcount == 1:
                    return taken
        raise RuntimeError("Could not reserve telemetry stream cursors")

    def _read(self, build):
        with self._guard:
            engine, table = self._open()
        with engine.connect() as connection:
            return connection.execute(build(table)).all()

    def put(self, row: dict[str, Any]) -> None:
        with self._guard:
            engine, table = self._open()
        with engine.begin() as connection:
            updated = connection.execute(
                table.update().where(table.c.trace_id == row["trace_id"]).values(**row)
            )
            if updated.rowcount == 0:
                connection.execute(table.insert().values(**row))

    def remove(self, trace_ids: set[str]) -> list[str]:
        if not trace_ids:
            return []
        with self._guard:
            engine, table = self._open()
        with engine.begin() as connection:
            bodies = [
                row[0]
                for row in connection.execute(
                    table.select()
                    .with_only_columns(table.c.body)
                    .where(table.c.trace_id.in_(sorted(trace_ids)))
                ).all()
            ]
            connection.execute(table.delete().where(table.c.trace_id.in_(sorted(trace_ids))))
        return bodies

    def contains(self, trace_id: str) -> bool:
        return bool(
            self._read(
                lambda table: table.select()
                .with_only_columns(table.c.trace_id)
                .where(table.c.trace_id == trace_id)
            )
        )

    def headers(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        def build(table):
            statement = table.select().with_only_columns(
                *(table.c[column] for column in HEADER_COLUMNS)
            )
            for column, expected in filters.items():
                statement = statement.where(table.c[column] == expected)
            return statement.order_by(table.c.started_at, table.c.trace_id)

        return [dict(zip(HEADER_COLUMNS, row)) for row in self._read(build)]

    def trace_ids_with_cursor_after(self, cursor: int) -> list[str]:
        return [
            row[0]
            for row in self._read(
                lambda table: table.select()
                .with_only_columns(table.c.trace_id)
                .where(table.c.last_cursor > cursor)
                .order_by(table.c.first_cursor)
            )
        ]

    def trace_ids_ended_before(self, cutoff: datetime) -> set[str]:
        return {
            row[0]
            for row in self._read(
                lambda table: table.select()
                .with_only_columns(table.c.trace_id)
                .where(table.c.ended_at.isnot(None), table.c.ended_at < cutoff.isoformat())
            )
        }

    def max_cursor(self) -> int:
        from sqlalchemy import func, select

        rows = self._read(lambda table: select(func.max(table.c.last_cursor)))
        return int(rows[0][0] or 0) if rows else 0

    def payload_references(self) -> set[str]:
        references: set[str] = set()
        for (text,) in self._read(
            lambda table: table.select().with_only_columns(table.c.payload_references)
        ):
            references.update(json.loads(text or "[]"))
        return references

    def close(self) -> None:
        with self._guard:
            engine, self._engine = self._engine, None
            self._table = None
        if engine is not None:
            engine.dispose()

    def drop(self) -> None:
        with self._guard:
            engine, table = self._open()
            metadata = self._metadata
        metadata.drop_all(engine)
