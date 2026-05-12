"""SQLite-backed task store for durable background execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import sqlite3
from pathlib import Path
from typing import TypeVar

from omnicoreagent.background.models import (
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundScheduleState,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
)
from omnicoreagent.background.store.in_memory import InMemoryTaskStore


T = TypeVar("T")


class SqlTaskStore(InMemoryTaskStore):
    """Durable local SQLite task store.

    The store reloads current SQLite state before each public operation. Mutating
    operations run under ``BEGIN IMMEDIATE`` so competing manager processes cannot
    claim or transition the same run from stale local state.
    """

    def __init__(self, url: str | None = None) -> None:
        super().__init__()
        self.url = url or "sqlite:///.omnicoreagent/background.db"
        self.path = self._path_from_url(self.url)
        self._schema_ready = False

    async def initialize(self) -> None:
        await super().initialize()
        await self._reload()

    async def save_agent(self, spec: BackgroundAgentSpec) -> None:
        await self._mutate(lambda: InMemoryTaskStore.save_agent(self, spec))

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        return await self._read(lambda: InMemoryTaskStore.get_agent(self, agent_id))

    async def delete_agent(self, agent_id: str) -> None:
        await self._mutate(lambda: InMemoryTaskStore.delete_agent(self, agent_id))

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        return await self._read(lambda: InMemoryTaskStore.list_agents(self))

    async def save_task(self, spec: BackgroundTaskSpec) -> None:
        await self._mutate(lambda: InMemoryTaskStore.save_task(self, spec))

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        return await self._read(lambda: InMemoryTaskStore.get_task(self, task_id))

    async def delete_task(self, task_id: str) -> None:
        await self._mutate(lambda: InMemoryTaskStore.delete_task(self, task_id))

    async def delete_runs_for_task(self, task_id: str) -> None:
        await self._mutate(
            lambda: InMemoryTaskStore.delete_runs_for_task(self, task_id)
        )

    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]:
        return await self._read(
            lambda: InMemoryTaskStore.list_tasks(self, agent_id=agent_id, enabled=enabled)
        )

    async def save_schedule_state(self, state: BackgroundScheduleState) -> None:
        await self._mutate(lambda: InMemoryTaskStore.save_schedule_state(self, state))

    async def set_schedule_paused(
        self, task_id: str, paused: bool
    ) -> BackgroundScheduleState:
        return await self._mutate(
            lambda: InMemoryTaskStore.set_schedule_paused(self, task_id, paused)
        )

    async def get_schedule_state(
        self, task_id: str
    ) -> BackgroundScheduleState | None:
        return await self._read(
            lambda: InMemoryTaskStore.get_schedule_state(self, task_id)
        )

    async def get_due_schedules(self, now, limit: int):
        return await self._read(
            lambda: InMemoryTaskStore.get_due_schedules(self, now, limit)
        )

    async def advance_schedule(
        self,
        task_id: str,
        expected_revision: int,
        occurrence_id: str,
        next_due_at,
    ) -> BackgroundScheduleState:
        return await self._mutate(
            lambda: InMemoryTaskStore.advance_schedule(
                self, task_id, expected_revision, occurrence_id, next_due_at
            )
        )

    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at,
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.dispatch_scheduled_run(
                self, run, overlap_policy, expected_schedule_revision, next_due_at
            )
        )

    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.create_run_with_overlap_guard(
                self, run, overlap_policy
            )
        )

    async def get_run(self, run_id: str) -> BackgroundRun | None:
        return await self._read(lambda: InMemoryTaskStore.get_run(self, run_id))

    async def update_run_metadata(
        self,
        run_id: str,
        patch: dict,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.update_run_metadata(
                self,
                run_id,
                patch,
                worker_id=worker_id,
                lease_token=lease_token,
            )
        )

    async def transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.transition_run(
                self,
                run_id,
                expected,
                next_status,
                patch=patch,
                worker_id=worker_id,
                lease_token=lease_token,
            )
        )

    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]:
        return await self._read(
            lambda: InMemoryTaskStore.list_runs(self, task_id=task_id, status=status)
        )

    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]:
        return await self._read(
            lambda: InMemoryTaskStore.list_active_runs(self, task_id=task_id)
        )

    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        return await self._read(
            lambda: InMemoryTaskStore.list_claimable_runs(self, limit)
        )

    async def claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None:
        return await self._mutate(
            lambda: InMemoryTaskStore.claim_next_run(self, worker_id, lease_seconds)
        )

    async def create_attempt(self, attempt: BackgroundAttempt) -> None:
        await self._mutate(lambda: InMemoryTaskStore.create_attempt(self, attempt))

    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        return await self._mutate(
            lambda: InMemoryTaskStore.update_attempt(
                self, attempt_id, patch, worker_id, lease_token
            )
        )

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        return await self._read(lambda: InMemoryTaskStore.list_attempts(self, run_id))

    async def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.claim_run(self, run_id, worker_id, lease_seconds)
        )

    async def steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        return await self._mutate(
            lambda: InMemoryTaskStore.steal_expired_run(
                self, run_id, worker_id, lease_seconds
            )
        )

    async def refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None:
        await self._mutate(
            lambda: InMemoryTaskStore.refresh_lease(
                self, run_id, worker_id, lease_token, lease_seconds
            )
        )

    async def release_lease(
        self, run_id: str, worker_id: str, lease_token: str
    ) -> None:
        await self._mutate(
            lambda: InMemoryTaskStore.release_lease(
                self, run_id, worker_id, lease_token
            )
        )

    async def list_expired_leases(self, now) -> list[BackgroundRun]:
        return await self._read(lambda: InMemoryTaskStore.list_expired_leases(self, now))

    async def request_cancel(self, run_id: str) -> None:
        await self._mutate(lambda: InMemoryTaskStore.request_cancel(self, run_id))

    async def is_cancel_requested(self, run_id: str) -> bool:
        return await self._read(
            lambda: InMemoryTaskStore.is_cancel_requested(self, run_id)
        )

    async def _read(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self._reload()
        return await operation()

    async def _mutate(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._ensure_schema()
        connection = self._connect()
        connection.isolation_level = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            await self._load_from_connection(connection)
            result = await operation()
            await self._persist_to_connection(connection)
            connection.execute("COMMIT")
            return result
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def _reload(self) -> None:
        self._ensure_schema()
        with self._connect() as connection:
            await self._load_from_connection(connection)

    async def _load_from_connection(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT kind, id, data FROM background_state").fetchall()
        async with self._lock:
            self._agents.clear()
            self._tasks.clear()
            self._schedule_states.clear()
            self._runs.clear()
            self._attempts.clear()
            self._cancel_requested.clear()
            for kind, record_id, data in rows:
                if kind == "agent":
                    self._agents[record_id] = BackgroundAgentSpec.model_validate_json(data)
                elif kind == "task":
                    self._tasks[record_id] = BackgroundTaskSpec.model_validate_json(data)
                elif kind == "schedule":
                    self._schedule_states[record_id] = (
                        BackgroundScheduleState.model_validate_json(data)
                    )
                elif kind == "run":
                    self._runs[record_id] = BackgroundRun.model_validate_json(data)
                elif kind == "attempt":
                    self._attempts[record_id] = BackgroundAttempt.model_validate_json(data)
                elif kind == "cancel":
                    self._cancel_requested.add(record_id)

    async def _persist_to_connection(self, connection: sqlite3.Connection) -> None:
        async with self._lock:
            records = []
            records.extend(
                ("agent", key, value.model_dump_json())
                for key, value in self._agents.items()
            )
            records.extend(
                ("task", key, value.model_dump_json())
                for key, value in self._tasks.items()
            )
            records.extend(
                ("schedule", key, value.model_dump_json())
                for key, value in self._schedule_states.items()
            )
            records.extend(
                ("run", key, value.model_dump_json())
                for key, value in self._runs.items()
            )
            records.extend(
                ("attempt", key, value.model_dump_json())
                for key, value in self._attempts.items()
            )
            records.extend(("cancel", run_id, "{}") for run_id in self._cancel_requested)

        connection.execute("DELETE FROM background_state")
        connection.executemany(
            "INSERT INTO background_state(kind, id, data) VALUES (?, ?, ?)",
            records,
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS background_state (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (kind, id)
                )
                """
            )
        self._schema_ready = True

    @staticmethod
    def _path_from_url(url: str) -> Path:
        if url in {"sqlite:///:memory:", ":memory:"}:
            return Path(":memory:")
        if url.startswith("sqlite:///"):
            return Path(url.removeprefix("sqlite:///")).expanduser()
        if url.startswith("sqlite://"):
            return Path(url.removeprefix("sqlite://")).expanduser()
        return Path(url).expanduser()
