"""Task-store interface for durable background execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from omnicoreagent.background.models import (
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundScheduleState,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
)


class AbstractTaskStore(ABC):
    """Operational source of truth for background agents, tasks, and runs."""

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def save_agent(self, spec: BackgroundAgentSpec) -> None: ...

    @abstractmethod
    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None: ...

    @abstractmethod
    async def delete_agent(self, agent_id: str) -> None: ...

    @abstractmethod
    async def list_agents(self) -> list[BackgroundAgentSpec]: ...

    @abstractmethod
    async def save_task(self, spec: BackgroundTaskSpec) -> None: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None: ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> None: ...

    @abstractmethod
    async def delete_runs_for_task(self, task_id: str) -> None: ...

    @abstractmethod
    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]: ...

    @abstractmethod
    async def save_schedule_state(self, state: BackgroundScheduleState) -> None: ...

    @abstractmethod
    async def get_schedule_state(
        self, task_id: str
    ) -> BackgroundScheduleState | None: ...

    @abstractmethod
    async def get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]: ...

    @abstractmethod
    async def advance_schedule(
        self,
        task_id: str,
        expected_revision: int,
        occurrence_id: str,
        next_due_at: datetime | None,
    ) -> BackgroundScheduleState: ...

    @abstractmethod
    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun: ...

    @abstractmethod
    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> BackgroundRun | None: ...

    @abstractmethod
    async def update_run_metadata(
        self,
        run_id: str,
        patch: dict,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun: ...

    @abstractmethod
    async def transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun: ...

    @abstractmethod
    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]: ...

    @abstractmethod
    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]: ...

    @abstractmethod
    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]: ...

    @abstractmethod
    async def claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None: ...

    @abstractmethod
    async def create_attempt(self, attempt: BackgroundAttempt) -> None: ...

    @abstractmethod
    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt: ...

    @abstractmethod
    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]: ...

    @abstractmethod
    async def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun: ...

    @abstractmethod
    async def steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun: ...

    @abstractmethod
    async def refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None: ...

    @abstractmethod
    async def release_lease(
        self, run_id: str, worker_id: str, lease_token: str
    ) -> None: ...

    @abstractmethod
    async def list_expired_leases(self, now: datetime) -> list[BackgroundRun]: ...

    @abstractmethod
    async def request_cancel(self, run_id: str) -> None: ...

    @abstractmethod
    async def is_cancel_requested(self, run_id: str) -> bool: ...
