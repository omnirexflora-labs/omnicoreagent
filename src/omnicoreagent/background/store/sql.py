"""A durable task store for any SQL database: one row per entity.

Scale plan, S2. What this replaced was a snapshot store: every operation
took a lock over the whole store, read all of its state, mutated it in
memory and wrote all of it back, using the SQLite file as the lock. That is
correct on one machine and costs more the longer a deployment runs — 56 ms
for one run write with a hundred runs kept, 344 ms with two thousand — and it
cannot be shared, because every worker would serialize behind the same lock
and rewrite every row.

Here a row is an entity and a write touches the row it writes. Reads are
queries with indexes behind them; ordering and filtering happen in the
database. Two workers on one database do not block each other except on the
same row: a run is claimed by an update that names the status and the version
it expected, so exactly one worker wins, and where the database has row locks
(`FOR UPDATE SKIP LOCKED` on PostgreSQL and MySQL) the losers do not wait for
it. SQLite has no such lock and does not need one: its writers are serialized
by an immediate transaction, with a busy timeout so a second process waits
rather than failing.

The idiom — SQLAlchemy Core, called from a worker thread, with versioned
compare-and-swap — is the one the SQL memory store already uses
(`core/memory_store/sql_db_memory.py`).

    SqlTaskStore("sqlite:///.omnicoreagent/background.db")   # the default
    SqlTaskStore("postgresql://user:pass@host/omnicoreagent")
    SqlTaskStore("mysql+pymysql://user:pass@host/omnicoreagent")

State written by the snapshot store is imported on first use, so a
deployment that upgrades keeps its agents, tasks, runs and attempts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from uuid import uuid4

from omnicoreagent.background.errors import (
    AgentNotFoundError,
    InvalidTaskStoreError,
    RunCancellationRequestedError,
    RunLeaseError,
    RunNotFoundError,
    TaskNotFoundError,
    TaskStoreError,
)
from omnicoreagent.background.models import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundScheduleState,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
    ScheduleType,
    build_occurrence_id,
    initial_schedule_due,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.core.sql_schema import create_tables

T = TypeVar("T")

# How many queued runs a claim looks at before giving up. A claim walks
# candidates in order until one is claimable and the update wins; more than
# this many blocked or contended runs ahead of it means the next poll can
# take its turn.
_CLAIM_CANDIDATES = 50

_UNFINISHED = {
    RunStatus.QUEUED,
    RunStatus.CLAIMED,
    RunStatus.RUNNING,
    RunStatus.RETRYING,
}
_LEASED = {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}

_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.CLAIMED, RunStatus.CANCELLED, RunStatus.SKIPPED},
    RunStatus.CLAIMED: {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.RUNNING: {
        RunStatus.RETRYING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.AWAITING_BUDGET,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    },
    RunStatus.RETRYING: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.AWAITING_APPROVAL: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.AWAITING_BUDGET: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.TIMEOUT: set(),
    RunStatus.SKIPPED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """A stored column value read back as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive(value: datetime | None) -> datetime | None:
    """UTC without a zone, which every database stores the same way.

    The authoritative value stays in the row's JSON; these columns exist to
    filter and order by.
    """
    value = _aware(value)
    return value.replace(tzinfo=None) if value is not None else None


def _run_order_key(run: BackgroundRun) -> tuple[datetime, datetime, str]:
    queued_at = run.queued_at or run.triggered_at
    return (queued_at, run.triggered_at, run.run_id)


class SqlTaskStore(AbstractTaskStore):
    """Durable task store over SQLAlchemy: SQLite, PostgreSQL or MySQL."""

    def __init__(
        self,
        url: str | None = None,
        *,
        table_prefix: str = "",
        busy_timeout_seconds: float = 30.0,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.url = self._normalize_url(url or "sqlite:///.omnicoreagent/background.db")
        # Several deployments can share one database; the prefix keeps their
        # tables apart, as the Redis and MongoDB stores' prefixes do.
        self.table_prefix = table_prefix
        self.busy_timeout_seconds = busy_timeout_seconds
        self.engine_kwargs = dict(engine_kwargs or {})
        self._engine: Any = None
        self._tables: Any = None
        self._closed = False

    # --- plumbing ---------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Accept a path or a URL; the old store took either."""
        if url in {":memory:", "sqlite:///:memory:", "sqlite://:memory:"}:
            return "sqlite://"
        if "://" in url:
            return url
        return f"sqlite:///{url}"

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    async def initialize(self) -> None:
        self._closed = False
        await asyncio.to_thread(self._open)

    async def close(self) -> None:
        self._closed = True
        engine, self._engine = self._engine, None
        if engine is not None:
            await asyncio.to_thread(engine.dispose)

    def _open(self) -> None:
        if self._engine is not None:
            return
        try:
            from sqlalchemy import create_engine, event
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise InvalidTaskStoreError(
                "The SQL task store requires SQLAlchemy: "
                "pip install 'omnicoreagent[postgres]'"
            ) from exc

        from omnicoreagent.background.store.sql_schema import build_tables

        kwargs: dict[str, Any] = {"future": True, **self.engine_kwargs}
        if self.is_sqlite:
            from sqlalchemy.pool import StaticPool

            connect_args = dict(kwargs.pop("connect_args", {}))
            connect_args.setdefault("check_same_thread", False)
            connect_args.setdefault("timeout", self.busy_timeout_seconds)
            kwargs["connect_args"] = connect_args
            if self.url == "sqlite://":
                # One shared connection, so an in-memory database survives
                # between the worker threads that use it.
                kwargs.setdefault("poolclass", StaticPool)
            self._ensure_sqlite_directory()

        try:
            engine = create_engine(self.url, **kwargs)
        except Exception as exc:
            raise InvalidTaskStoreError(f"Cannot open the SQL task store: {exc}") from exc

        if self.is_sqlite:

            @event.listens_for(engine, "connect")
            def _sqlite_setup(connection, _record):  # pragma: no cover - driver hook
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_seconds * 1000)}")
                cursor.close()

            @event.listens_for(engine, "begin")
            def _sqlite_immediate(connection):  # pragma: no cover - driver hook
                # Take the write lock at the start of the transaction, so a
                # read-then-write sequence cannot lose its read to another
                # process between the two.
                connection.exec_driver_sql("BEGIN IMMEDIATE")

        self._tables = build_tables(self.table_prefix)
        try:
            # Another process may be creating the same tables right now.
            create_tables(engine, self._tables.metadata)
        except Exception as exc:
            engine.dispose()
            raise InvalidTaskStoreError(
                f"Cannot create the SQL task store schema: {exc}"
            ) from exc
        self._engine = engine
        self._import_snapshot_state()

    def _ensure_sqlite_directory(self) -> None:
        from pathlib import Path

        path = self.url.removeprefix("sqlite:///")
        if path and path != "sqlite://":
            parent = Path(path).expanduser().parent
            if str(parent) not in {"", "."}:
                parent.mkdir(parents=True, exist_ok=True)

    async def _call(self, operation: Callable[..., T], *args: Any) -> T:
        if self._engine is None:
            await asyncio.to_thread(self._open)
        return await asyncio.to_thread(operation, *args)

    def _transaction(self):
        return self._engine.begin()

    def _connection(self):
        return self._engine.connect()

    def _locked(self, statement):
        """Row locks where the database has them; SQLite serializes instead."""
        if self.is_sqlite:
            return statement
        return statement.with_for_update(skip_locked=True)

    # --- rows -------------------------------------------------------------

    def _agent_row(self, spec: BackgroundAgentSpec) -> dict[str, Any]:
        return {
            "agent_id": spec.agent_id,
            "created_at": _naive(getattr(spec, "created_at", None)),
            "data": spec.model_dump_json(),
        }

    def _task_row(self, spec: BackgroundTaskSpec) -> dict[str, Any]:
        return {
            "task_id": spec.task_id,
            "agent_id": spec.agent_id,
            "enabled": bool(spec.enabled),
            "created_at": _naive(getattr(spec, "created_at", None)),
            "data": spec.model_dump_json(),
        }

    def _schedule_row(self, state: BackgroundScheduleState) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "next_due_at": _naive(state.next_due_at),
            "paused": bool(state.paused),
            "schedule_revision": state.schedule_revision,
            "data": state.model_dump_json(),
        }

    def _run_row(self, run: BackgroundRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "status": run.status.value,
            "occurrence_id": run.occurrence_id,
            "queued_at": _naive(run.queued_at or run.triggered_at),
            "triggered_at": _naive(run.triggered_at),
            "lease_expires_at": _naive(run.lease_expires_at),
            "cancel_requested_at": _naive(run.cancel_requested_at),
            "data": run.model_dump_json(),
        }

    def _attempt_row(self, attempt: BackgroundAttempt) -> dict[str, Any]:
        return {
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.run_id,
            "attempt_number": attempt.attempt_number,
            "data": attempt.model_dump_json(),
        }

    @staticmethod
    def _model(kind, row) -> Any:
        return kind.model_validate_json(row.data)

    # --- agents -----------------------------------------------------------

    async def save_agent(self, spec: BackgroundAgentSpec) -> None:
        await self._call(self._save_agent, spec)

    def _save_agent(self, spec: BackgroundAgentSpec) -> None:
        agents = self._tables.agents
        row = self._agent_row(spec)
        with self._transaction() as connection:
            updated = connection.execute(
                agents.update()
                .where(agents.c.agent_id == spec.agent_id)
                .values(**row, version=agents.c.version + 1)
            )
            if updated.rowcount == 0:
                connection.execute(agents.insert().values(**row, version=1))

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        return await self._call(self._get_agent, agent_id)

    def _get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        agents = self._tables.agents
        with self._connection() as connection:
            row = connection.execute(
                agents.select().where(agents.c.agent_id == agent_id)
            ).first()
        return self._model(BackgroundAgentSpec, row) if row else None

    async def delete_agent(self, agent_id: str) -> None:
        await self._call(self._delete_agent, agent_id)

    def _delete_agent(self, agent_id: str) -> None:
        agents = self._tables.agents
        with self._transaction() as connection:
            connection.execute(agents.delete().where(agents.c.agent_id == agent_id))

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        return await self._call(self._list_agents)

    def _list_agents(self) -> list[BackgroundAgentSpec]:
        agents = self._tables.agents
        with self._connection() as connection:
            rows = connection.execute(agents.select().order_by(agents.c.created_at)).all()
        return [self._model(BackgroundAgentSpec, row) for row in rows]

    # --- tasks ------------------------------------------------------------

    async def save_task(self, spec: BackgroundTaskSpec) -> None:
        await self._call(self._save_task, spec)

    def _save_task(self, spec: BackgroundTaskSpec) -> None:
        agents, tasks, schedules = (
            self._tables.agents,
            self._tables.tasks,
            self._tables.schedules,
        )
        row = self._task_row(spec)
        with self._transaction() as connection:
            if not connection.execute(
                agents.select().where(agents.c.agent_id == spec.agent_id)
            ).first():
                raise AgentNotFoundError(f"Agent not found: {spec.agent_id}")
            existing_row = connection.execute(
                tasks.select().where(tasks.c.task_id == spec.task_id)
            ).first()
            existing = self._model(BackgroundTaskSpec, existing_row) if existing_row else None
            if existing is None:
                connection.execute(tasks.insert().values(**row, version=1))
            else:
                connection.execute(
                    tasks.update()
                    .where(tasks.c.task_id == spec.task_id)
                    .values(**row, version=tasks.c.version + 1)
                )
            if existing is not None and existing.schedule == spec.schedule:
                return
            current = connection.execute(
                schedules.select().where(schedules.c.task_id == spec.task_id)
            ).first()
            revision = 1
            if existing is not None and current is not None:
                revision = (
                    self._model(BackgroundScheduleState, current).schedule_revision + 1
                )
            state = BackgroundScheduleState(
                task_id=spec.task_id,
                next_due_at=initial_schedule_due(spec.schedule, _now()),
                schedule_revision=revision,
            )
            state_row = self._schedule_row(state)
            if current is None:
                connection.execute(schedules.insert().values(**state_row, version=1))
            else:
                connection.execute(
                    schedules.update()
                    .where(schedules.c.task_id == spec.task_id)
                    .values(**state_row, version=schedules.c.version + 1)
                )

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        return await self._call(self._get_task, task_id)

    def _get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        tasks = self._tables.tasks
        with self._connection() as connection:
            row = connection.execute(tasks.select().where(tasks.c.task_id == task_id)).first()
        return self._model(BackgroundTaskSpec, row) if row else None

    async def delete_task(self, task_id: str) -> None:
        await self._call(self._delete_task, task_id)

    def _delete_task(self, task_id: str) -> None:
        tasks, schedules = self._tables.tasks, self._tables.schedules
        with self._transaction() as connection:
            connection.execute(tasks.delete().where(tasks.c.task_id == task_id))
            connection.execute(schedules.delete().where(schedules.c.task_id == task_id))

    async def delete_runs_for_task(self, task_id: str) -> None:
        await self._call(self._delete_runs_for_task, task_id)

    def _delete_runs_for_task(self, task_id: str) -> None:
        runs, attempts = self._tables.runs, self._tables.attempts
        with self._transaction() as connection:
            run_ids = [
                row.run_id
                for row in connection.execute(
                    runs.select().with_only_columns(runs.c.run_id).where(
                        runs.c.task_id == task_id
                    )
                ).all()
            ]
            if run_ids:
                connection.execute(attempts.delete().where(attempts.c.run_id.in_(run_ids)))
            connection.execute(runs.delete().where(runs.c.task_id == task_id))

    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]:
        return await self._call(self._list_tasks, agent_id, enabled)

    def _list_tasks(
        self, agent_id: str | None, enabled: bool | None
    ) -> list[BackgroundTaskSpec]:
        tasks = self._tables.tasks
        statement = tasks.select().order_by(tasks.c.created_at)
        if agent_id is not None:
            statement = statement.where(tasks.c.agent_id == agent_id)
        if enabled is not None:
            statement = statement.where(tasks.c.enabled == bool(enabled))
        with self._connection() as connection:
            rows = connection.execute(statement).all()
        return [self._model(BackgroundTaskSpec, row) for row in rows]

    # --- schedules --------------------------------------------------------

    async def save_schedule_state(self, state: BackgroundScheduleState) -> None:
        await self._call(self._save_schedule_state, state)

    def _save_schedule_state(self, state: BackgroundScheduleState) -> None:
        tasks, schedules = self._tables.tasks, self._tables.schedules
        row = self._schedule_row(state)
        with self._transaction() as connection:
            if not connection.execute(
                tasks.select().where(tasks.c.task_id == state.task_id)
            ).first():
                raise TaskNotFoundError(f"Task not found: {state.task_id}")
            updated = connection.execute(
                schedules.update()
                .where(schedules.c.task_id == state.task_id)
                .values(**row, version=schedules.c.version + 1)
            )
            if updated.rowcount == 0:
                connection.execute(schedules.insert().values(**row, version=1))

    async def set_schedule_paused(
        self, task_id: str, paused: bool, *, reason: str | None = None
    ) -> BackgroundScheduleState:
        return await self._call(self._set_schedule_paused, task_id, paused, reason)

    def _set_schedule_paused(
        self, task_id: str, paused: bool, reason: str | None
    ) -> BackgroundScheduleState:
        with self._transaction() as connection:
            state, version = self._require_schedule(connection, task_id)
            updated = state.model_copy(
                update={
                    "paused": paused,
                    "paused_reason": reason if paused else None,
                    "updated_at": _now(),
                },
                deep=True,
            )
            self._write_schedule(connection, updated, version)
            return updated

    async def get_schedule_state(self, task_id: str) -> BackgroundScheduleState | None:
        return await self._call(self._get_schedule_state, task_id)

    def _get_schedule_state(self, task_id: str) -> BackgroundScheduleState | None:
        schedules = self._tables.schedules
        with self._connection() as connection:
            row = connection.execute(
                schedules.select().where(schedules.c.task_id == task_id)
            ).first()
        return self._model(BackgroundScheduleState, row) if row else None

    async def get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]:
        return await self._call(self._get_due_schedules, now, limit)

    def _get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]:
        tasks, schedules = self._tables.tasks, self._tables.schedules
        cutoff = _naive(now)
        statement = (
            tasks.select()
            .add_columns(schedules.c.data.label("state_data"))
            .join(schedules, schedules.c.task_id == tasks.c.task_id)
            .where(
                tasks.c.enabled.is_(True),
                schedules.c.paused.is_(False),
                schedules.c.next_due_at.isnot(None),
                schedules.c.next_due_at <= cutoff,
            )
            .order_by(tasks.c.created_at)
        )
        with self._connection() as connection:
            rows = connection.execute(statement).all()
        due: list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]] = []
        for row in rows:
            if len(due) >= limit:
                break
            task = self._model(BackgroundTaskSpec, row)
            if task.schedule.type == ScheduleType.MANUAL:
                continue
            state = BackgroundScheduleState.model_validate_json(row.state_data)
            due.append(
                (
                    task,
                    state,
                    build_occurrence_id(
                        task.schedule.type, state.schedule_revision, state.next_due_at
                    ),
                )
            )
        return due

    async def advance_schedule(
        self,
        task_id: str,
        expected_revision: int,
        occurrence_id: str,
        next_due_at: datetime | None,
    ) -> BackgroundScheduleState:
        return await self._call(
            self._advance_schedule, task_id, expected_revision, next_due_at
        )

    def _advance_schedule(
        self, task_id: str, expected_revision: int, next_due_at: datetime | None
    ) -> BackgroundScheduleState:
        with self._transaction() as connection:
            state, version = self._require_schedule(connection, task_id)
            if state.schedule_revision != expected_revision:
                raise TaskStoreError("Schedule revision mismatch")
            advanced = self._advanced_state(state, next_due_at)
            self._write_schedule(connection, advanced, version)
            return advanced

    @staticmethod
    def _advanced_state(
        state: BackgroundScheduleState, next_due_at: datetime | None
    ) -> BackgroundScheduleState:
        return state.model_copy(
            update={
                "last_due_at": state.next_due_at,
                "last_dispatched_at": _now(),
                "next_due_at": _aware(next_due_at),
                "updated_at": _now(),
            },
            deep=True,
        )

    def _require_schedule(
        self, connection, task_id: str
    ) -> tuple[BackgroundScheduleState, int]:
        schedules = self._tables.schedules
        row = connection.execute(
            schedules.select().where(schedules.c.task_id == task_id)
        ).first()
        if row is None:
            raise TaskNotFoundError(f"Schedule state not found for task: {task_id}")
        return self._model(BackgroundScheduleState, row), row.version

    def _write_schedule(
        self, connection, state: BackgroundScheduleState, expected_version: int
    ) -> None:
        schedules = self._tables.schedules
        result = connection.execute(
            schedules.update()
            .where(
                schedules.c.task_id == state.task_id,
                schedules.c.version == expected_version,
            )
            .values(**self._schedule_row(state), version=expected_version + 1)
        )
        if result.rowcount != 1:
            raise TaskStoreError(
                f"Schedule for task {state.task_id} changed since it was read"
            )

    # --- runs -------------------------------------------------------------

    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun:
        return await self._call(
            self._dispatch_scheduled_run,
            run,
            overlap_policy,
            expected_schedule_revision,
            next_due_at,
        )

    def _dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun:
        runs = self._tables.runs
        if not run.occurrence_id:
            raise TaskStoreError("Scheduled runs require occurrence_id")
        with self._transaction() as connection:
            state, version = self._require_schedule(connection, run.task_id)
            if state.schedule_revision != expected_schedule_revision:
                raise TaskStoreError("Schedule revision mismatch")
            existing = connection.execute(
                runs.select().where(
                    runs.c.task_id == run.task_id,
                    runs.c.occurrence_id == run.occurrence_id,
                )
            ).first()
            if existing is not None:
                return self._model(BackgroundRun, existing)
            created = self._create_run(connection, run, overlap_policy)
            self._write_schedule(connection, self._advanced_state(state, next_due_at), version)
            return created

    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        return await self._call(self._create_run_with_overlap_guard, run, overlap_policy)

    def _create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        with self._transaction() as connection:
            return self._create_run(connection, run, overlap_policy)

    def _create_run(
        self, connection, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        """The overlap guard, reading only the task's unfinished runs."""
        runs, tasks = self._tables.runs, self._tables.tasks
        if not connection.execute(
            tasks.select().where(tasks.c.task_id == run.task_id)
        ).first():
            raise TaskNotFoundError(f"Task not found: {run.task_id}")
        if connection.execute(runs.select().where(runs.c.run_id == run.run_id)).first():
            raise TaskStoreError(f"Run already exists: {run.run_id}")
        active_rows = connection.execute(
            runs.select().where(
                runs.c.task_id == run.task_id,
                runs.c.status.in_([status.value for status in ACTIVE_RUN_STATUSES]),
            )
        ).all()
        if active_rows and overlap_policy == OverlapPolicy.SKIP_IF_RUNNING:
            skipped = run.model_copy(
                update={"status": RunStatus.SKIPPED, "finished_at": _now()}, deep=True
            )
            connection.execute(runs.insert().values(**self._run_row(skipped), version=1))
            return skipped
        if active_rows and overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            for row in active_rows:
                active = self._model(BackgroundRun, row)
                cancelled = active.model_copy(
                    update={"cancel_requested_at": _now()}, deep=True
                )
                self._write_run(connection, cancelled, row.version)
        connection.execute(runs.insert().values(**self._run_row(run), version=1))
        return run

    def _write_run(
        self, connection, run: BackgroundRun, expected_version: int
    ) -> BackgroundRun:
        runs = self._tables.runs
        result = connection.execute(
            runs.update()
            .where(runs.c.run_id == run.run_id, runs.c.version == expected_version)
            .values(**self._run_row(run), version=expected_version + 1)
        )
        if result.rowcount != 1:
            raise TaskStoreError(f"Run {run.run_id} changed since it was read")
        return run

    def _require_run(self, connection, run_id: str) -> tuple[BackgroundRun, int]:
        runs = self._tables.runs
        row = connection.execute(runs.select().where(runs.c.run_id == run_id)).first()
        if row is None:
            raise RunNotFoundError(f"Run not found: {run_id}")
        return self._model(BackgroundRun, row), row.version

    async def get_run(self, run_id: str) -> BackgroundRun | None:
        return await self._call(self._get_run, run_id)

    def _get_run(self, run_id: str) -> BackgroundRun | None:
        runs = self._tables.runs
        with self._connection() as connection:
            row = connection.execute(runs.select().where(runs.c.run_id == run_id)).first()
        return self._model(BackgroundRun, row) if row else None

    async def update_run_metadata(
        self,
        run_id: str,
        patch: dict,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        return await self._call(
            self._update_run_metadata, run_id, patch, worker_id, lease_token
        )

    def _update_run_metadata(
        self, run_id: str, patch: dict, worker_id: str | None, lease_token: str | None
    ) -> BackgroundRun:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            if worker_id is not None or lease_token is not None:
                self._verify_lease(run, worker_id, lease_token)
            updated = run.model_copy(
                update={"metadata": {**run.metadata, **patch}}, deep=True
            )
            return self._write_run(connection, updated, version)

    async def transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        return await self._call(
            self._transition_run, run_id, expected, next_status, patch, worker_id, lease_token
        )

    def _transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None,
        worker_id: str | None,
        lease_token: str | None,
    ) -> BackgroundRun:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            if run.status not in expected:
                raise TaskStoreError(
                    f"Run {run_id} status {run.status.value} not in expected states"
                )
            if next_status not in _ALLOWED_TRANSITIONS[run.status]:
                raise TaskStoreError(
                    f"Invalid run transition {run.status.value} -> {next_status.value}"
                )
            if run.status in {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}:
                self._verify_lease(run, worker_id, lease_token)
            if (
                next_status
                in {RunStatus.COMPLETED, RunStatus.RETRYING, RunStatus.QUEUED}
                and run.cancel_requested_at is not None
            ):
                raise RunCancellationRequestedError(
                    "Run cancellation requested before non-terminal transition"
                )
            update = {**(patch or {}), "status": next_status}
            if next_status in TERMINAL_RUN_STATUSES:
                update.setdefault("finished_at", _now())
                update.setdefault("lease_owner", None)
                update.setdefault("lease_token", None)
                update.setdefault("lease_expires_at", None)
            return self._write_run(
                connection, run.model_copy(update=update, deep=True), version
            )

    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]:
        return await self._call(self._list_runs, task_id, status)

    def _list_runs(
        self, task_id: str | None, status: RunStatus | None
    ) -> list[BackgroundRun]:
        runs = self._tables.runs
        statement = runs.select().order_by(runs.c.queued_at, runs.c.run_id)
        if task_id is not None:
            statement = statement.where(runs.c.task_id == task_id)
        if status is not None:
            statement = statement.where(runs.c.status == status.value)
        with self._connection() as connection:
            rows = connection.execute(statement).all()
        return [self._model(BackgroundRun, row) for row in rows]

    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]:
        return await self._call(self._list_active_runs, task_id)

    def _list_active_runs(self, task_id: str | None) -> list[BackgroundRun]:
        runs = self._tables.runs
        statement = (
            runs.select()
            .where(runs.c.status.in_([status.value for status in ACTIVE_RUN_STATUSES]))
            .order_by(runs.c.queued_at, runs.c.run_id)
        )
        if task_id is not None:
            statement = statement.where(runs.c.task_id == task_id)
        with self._connection() as connection:
            rows = connection.execute(statement).all()
        return [self._model(BackgroundRun, row) for row in rows]

    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        return await self._call(self._list_claimable_runs, limit)

    def _list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        with self._connection() as connection:
            return [
                run for run, _ in self._claimable(connection, limit)
            ]

    def _claimable(self, connection, limit: int) -> list[tuple[BackgroundRun, int]]:
        """Queued runs whose turn it is, in order, with their row versions."""
        runs, tasks = self._tables.runs, self._tables.tasks
        rows = connection.execute(
            runs.select()
            .where(
                runs.c.status == RunStatus.QUEUED.value,
                runs.c.queued_at <= _naive(_now()),
            )
            .order_by(runs.c.queued_at, runs.c.triggered_at, runs.c.run_id)
            .limit(max(limit, 1) + _CLAIM_CANDIDATES)
        ).all()
        claimable: list[tuple[BackgroundRun, int]] = []
        policies: dict[str, OverlapPolicy] = {}
        for row in rows:
            if len(claimable) >= limit:
                break
            run = self._model(BackgroundRun, row)
            if run.task_id not in policies:
                task_row = connection.execute(
                    tasks.select().where(tasks.c.task_id == run.task_id)
                ).first()
                if task_row is None:
                    continue
                policies[run.task_id] = self._model(
                    BackgroundTaskSpec, task_row
                ).overlap_policy
            if policies[run.task_id] == OverlapPolicy.QUEUE_NEXT and self._has_earlier_work(
                connection, run
            ):
                continue
            claimable.append((run, row.version))
        return claimable

    def _has_earlier_work(self, connection, run: BackgroundRun) -> bool:
        """Is another unfinished run of this task ahead of this one?"""
        runs = self._tables.runs
        order = _run_order_key(run)
        rows = connection.execute(
            runs.select()
            .where(
                runs.c.task_id == run.task_id,
                runs.c.run_id != run.run_id,
                runs.c.status.in_([status.value for status in _UNFINISHED]),
                runs.c.queued_at <= _naive(order[0]),
            )
            .order_by(runs.c.queued_at, runs.c.run_id)
            .limit(50)
        ).all()
        return any(
            _run_order_key(self._model(BackgroundRun, row)) < order for row in rows
        )

    async def claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None:
        return await self._call(self._claim_next_run, worker_id, lease_seconds)

    def _claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None:
        with self._transaction() as connection:
            for run, version in self._claimable(connection, _CLAIM_CANDIDATES):
                claimed = self._leased(run, worker_id, lease_seconds, claim=True)
                try:
                    return self._write_run(connection, claimed, version)
                except TaskStoreError:
                    continue  # another worker took it; try the next one
        return None

    async def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        return await self._call(self._claim_run, run_id, worker_id, lease_seconds)

    def _claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> BackgroundRun:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            if run.status != RunStatus.QUEUED:
                raise RunLeaseError(f"Run {run_id} is not queued")
            claimable = {item.run_id for item, _ in self._claimable(connection, _CLAIM_CANDIDATES)}
            if run_id not in claimable:
                raise RunLeaseError(f"Run {run_id} is blocked by overlap policy")
            claimed = self._leased(run, worker_id, lease_seconds, claim=True)
            try:
                return self._write_run(connection, claimed, version)
            except TaskStoreError as exc:
                raise RunLeaseError(f"Run {run_id} was claimed by another worker") from exc

    async def steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        return await self._call(self._steal_expired_run, run_id, worker_id, lease_seconds)

    def _steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            if not run.lease_expires_at or run.lease_expires_at > _now():
                raise RunLeaseError(f"Run {run_id} lease has not expired")
            stolen = self._leased(run, worker_id, lease_seconds, claim=False)
            try:
                return self._write_run(connection, stolen, version)
            except TaskStoreError as exc:
                raise RunLeaseError(f"Run {run_id} was taken by another worker") from exc

    @staticmethod
    def _leased(
        run: BackgroundRun, worker_id: str, lease_seconds: int, *, claim: bool
    ) -> BackgroundRun:
        update = {
            "lease_owner": worker_id,
            "lease_token": uuid4().hex,
            "lease_generation": run.lease_generation + 1,
            "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
            "heartbeat_at": _now(),
        }
        if claim:
            update["status"] = RunStatus.CLAIMED
            update["claimed_at"] = _now()
        return run.model_copy(update=update, deep=True)

    async def refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None:
        await self._call(self._refresh_lease, run_id, worker_id, lease_token, lease_seconds)

    def _refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            self._verify_lease(run, worker_id, lease_token)
            self._write_run(
                connection,
                run.model_copy(
                    update={
                        "heartbeat_at": _now(),
                        "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
                    },
                    deep=True,
                ),
                version,
            )

    async def release_lease(self, run_id: str, worker_id: str, lease_token: str) -> None:
        await self._call(self._release_lease, run_id, worker_id, lease_token)

    def _release_lease(self, run_id: str, worker_id: str, lease_token: str) -> None:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            self._verify_lease(run, worker_id, lease_token)
            self._write_run(
                connection,
                run.model_copy(
                    update={
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                    },
                    deep=True,
                ),
                version,
            )

    async def list_expired_leases(self, now: datetime) -> list[BackgroundRun]:
        return await self._call(self._list_expired_leases, now)

    def _list_expired_leases(self, now: datetime) -> list[BackgroundRun]:
        runs = self._tables.runs
        with self._connection() as connection:
            rows = connection.execute(
                runs.select()
                .where(
                    runs.c.status.in_([status.value for status in _LEASED]),
                    runs.c.lease_expires_at.isnot(None),
                    runs.c.lease_expires_at <= _naive(now),
                )
                .order_by(runs.c.lease_expires_at, runs.c.run_id)
            ).all()
        return [self._model(BackgroundRun, row) for row in rows]

    async def request_cancel(self, run_id: str) -> None:
        await self._call(self._request_cancel, run_id)

    def _request_cancel(self, run_id: str) -> None:
        with self._transaction() as connection:
            run, version = self._require_run(connection, run_id)
            self._write_run(
                connection,
                run.model_copy(update={"cancel_requested_at": _now()}, deep=True),
                version,
            )

    async def is_cancel_requested(self, run_id: str) -> bool:
        return await self._call(self._is_cancel_requested, run_id)

    def _is_cancel_requested(self, run_id: str) -> bool:
        runs = self._tables.runs
        with self._connection() as connection:
            row = connection.execute(
                runs.select()
                .with_only_columns(runs.c.cancel_requested_at)
                .where(runs.c.run_id == run_id)
            ).first()
        return bool(row and row.cancel_requested_at is not None)

    # --- attempts ---------------------------------------------------------

    async def create_attempt(self, attempt: BackgroundAttempt) -> None:
        await self._call(self._create_attempt, attempt)

    def _create_attempt(self, attempt: BackgroundAttempt) -> None:
        attempts = self._tables.attempts
        with self._transaction() as connection:
            run, _ = self._require_run(connection, attempt.run_id)
            self._verify_lease(run, attempt.worker_id, attempt.lease_token)
            if connection.execute(
                attempts.select().where(attempts.c.attempt_id == attempt.attempt_id)
            ).first():
                raise TaskStoreError(f"Attempt already exists: {attempt.attempt_id}")
            connection.execute(
                attempts.insert().values(**self._attempt_row(attempt), version=1)
            )

    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        return await self._call(
            self._update_attempt, attempt_id, patch, worker_id, lease_token
        )

    def _update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        attempts = self._tables.attempts
        with self._transaction() as connection:
            row = connection.execute(
                attempts.select().where(attempts.c.attempt_id == attempt_id)
            ).first()
            if row is None:
                raise TaskStoreError(f"Attempt not found: {attempt_id}")
            attempt = self._model(BackgroundAttempt, row)
            run, _ = self._require_run(connection, attempt.run_id)
            self._verify_lease(run, worker_id, lease_token)
            updated = attempt.model_copy(update=patch, deep=True)
            result = connection.execute(
                attempts.update()
                .where(
                    attempts.c.attempt_id == attempt_id,
                    attempts.c.version == row.version,
                )
                .values(**self._attempt_row(updated), version=row.version + 1)
            )
            if result.rowcount != 1:
                raise TaskStoreError(f"Attempt {attempt_id} changed since it was read")
            return updated

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        return await self._call(self._list_attempts, run_id)

    def _list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        attempts = self._tables.attempts
        with self._connection() as connection:
            rows = connection.execute(
                attempts.select()
                .where(attempts.c.run_id == run_id)
                .order_by(attempts.c.attempt_number)
            ).all()
        return [self._model(BackgroundAttempt, row) for row in rows]

    # --- leases -----------------------------------------------------------

    @staticmethod
    def _verify_lease(
        run: BackgroundRun, worker_id: str | None, lease_token: str | None
    ) -> None:
        if not worker_id or not lease_token:
            raise RunLeaseError("worker_id and lease_token are required")
        if run.lease_owner != worker_id or run.lease_token != lease_token:
            raise RunLeaseError("Run lease token mismatch")
        if run.lease_expires_at is not None and run.lease_expires_at <= _now():
            raise RunLeaseError("Run lease has expired")

    # --- the snapshot store's state ---------------------------------------

    def _import_snapshot_state(self) -> None:
        """Take over what the snapshot store wrote, once.

        The old store kept everything in one ``background_state`` table. If it
        is there and the new tables are empty, its rows are imported and the
        old table is left alone, so a downgrade still finds its own state.
        """
        from sqlalchemy import inspect, text

        with self._engine.begin() as connection:
            if not inspect(connection).has_table(f"{self.table_prefix}background_state"):
                return
            runs, tasks = self._tables.runs, self._tables.tasks
            held = connection.execute(
                tasks.select().with_only_columns(tasks.c.task_id).limit(1)
            ).first() or connection.execute(
                runs.select().with_only_columns(runs.c.run_id).limit(1)
            ).first()
            if held is not None:
                return
            rows = connection.execute(
                text(f"SELECT kind, id, data FROM {self.table_prefix}background_state")
            ).all()
            imported = {
                "agent": (self._tables.agents, BackgroundAgentSpec, self._agent_row),
                "task": (tasks, BackgroundTaskSpec, self._task_row),
                "schedule": (
                    self._tables.schedules,
                    BackgroundScheduleState,
                    self._schedule_row,
                ),
                "run": (runs, BackgroundRun, self._run_row),
                "attempt": (
                    self._tables.attempts,
                    BackgroundAttempt,
                    self._attempt_row,
                ),
            }
            cancelled: set[str] = set()
            for kind, record_id, data in rows:
                if kind == "cancel":
                    cancelled.add(record_id)
                    continue
                entry = imported.get(kind)
                if entry is None:
                    continue
                table, model, to_row = entry
                connection.execute(
                    table.insert().values(**to_row(model.model_validate_json(data)), version=1)
                )
            for run_id in cancelled:
                connection.execute(
                    runs.update()
                    .where(runs.c.run_id == run_id, runs.c.cancel_requested_at.is_(None))
                    .values(cancel_requested_at=_naive(_now()))
                )
