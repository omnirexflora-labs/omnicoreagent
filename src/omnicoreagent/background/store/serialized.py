"""Shared serialized task-store wrapper for remote durable backends."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

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


class SerializedTaskStore(InMemoryTaskStore):
    """Persist in-memory task-store state through backend load/save hooks."""

    def __init__(self) -> None:
        super().__init__()
        self._operation_lock = asyncio.Lock()

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
        async with self._operation_lock:
            await self._load_backend_state()
            return await operation()

    @abstractmethod
    async def _mutate(self, operation: Callable[[], Awaitable[T]]) -> T: ...

    @abstractmethod
    async def _load_backend_state(self) -> None: ...

    async def _load_snapshot(self, snapshot: dict[str, Any] | None) -> None:
        snapshot = snapshot or {}
        async with self._lock:
            self._agents = {
                key: BackgroundAgentSpec.model_validate(value)
                for key, value in snapshot.get("agents", {}).items()
            }
            self._tasks = {
                key: BackgroundTaskSpec.model_validate(value)
                for key, value in snapshot.get("tasks", {}).items()
            }
            self._schedule_states = {
                key: BackgroundScheduleState.model_validate(value)
                for key, value in snapshot.get("schedule_states", {}).items()
            }
            self._runs = {
                key: BackgroundRun.model_validate(value)
                for key, value in snapshot.get("runs", {}).items()
            }
            self._attempts = {
                key: BackgroundAttempt.model_validate(value)
                for key, value in snapshot.get("attempts", {}).items()
            }
            self._cancel_requested = set(snapshot.get("cancel_requested", []))

    async def _snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "agents": {
                    key: value.model_dump(mode="json")
                    for key, value in self._agents.items()
                },
                "tasks": {
                    key: value.model_dump(mode="json")
                    for key, value in self._tasks.items()
                },
                "schedule_states": {
                    key: value.model_dump(mode="json")
                    for key, value in self._schedule_states.items()
                },
                "runs": {
                    key: value.model_dump(mode="json")
                    for key, value in self._runs.items()
                },
                "attempts": {
                    key: value.model_dump(mode="json")
                    for key, value in self._attempts.items()
                },
                "cancel_requested": sorted(self._cancel_requested),
            }
