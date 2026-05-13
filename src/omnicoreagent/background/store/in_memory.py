"""In-memory task store for development and tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from omnicoreagent.background.errors import (
    AgentNotFoundError,
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
    utc_now,
)
from omnicoreagent.background.store.base import AbstractTaskStore


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
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    },
    RunStatus.RETRYING: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.TIMEOUT: set(),
    RunStatus.SKIPPED: set(),
}


def _copy_model(model):
    return model.model_copy(deep=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryTaskStore(AbstractTaskStore):
    """Async in-memory implementation of the background task-store contract."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._agents: dict[str, BackgroundAgentSpec] = {}
        self._tasks: dict[str, BackgroundTaskSpec] = {}
        self._schedule_states: dict[str, BackgroundScheduleState] = {}
        self._runs: dict[str, BackgroundRun] = {}
        self._attempts: dict[str, BackgroundAttempt] = {}
        self._cancel_requested: set[str] = set()
        self._closed = False

    async def initialize(self) -> None:
        self._closed = False

    async def close(self) -> None:
        self._closed = True

    async def save_agent(self, spec: BackgroundAgentSpec) -> None:
        async with self._lock:
            self._agents[spec.agent_id] = _copy_model(spec)

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        async with self._lock:
            agent = self._agents.get(agent_id)
            return _copy_model(agent) if agent else None

    async def delete_agent(self, agent_id: str) -> None:
        async with self._lock:
            self._agents.pop(agent_id, None)

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        async with self._lock:
            return [_copy_model(agent) for agent in self._sorted(self._agents.values())]

    async def save_task(self, spec: BackgroundTaskSpec) -> None:
        async with self._lock:
            if spec.agent_id not in self._agents:
                raise AgentNotFoundError(f"Agent not found: {spec.agent_id}")
            existing = self._tasks.get(spec.task_id)
            self._tasks[spec.task_id] = _copy_model(spec)
            if existing is None:
                self._schedule_states[spec.task_id] = self._initial_schedule_state(spec)
                return
            if existing.schedule != spec.schedule:
                current = self._schedule_states.get(spec.task_id)
                revision = (current.schedule_revision + 1) if current else 1
                self._schedule_states[spec.task_id] = self._initial_schedule_state(
                    spec, schedule_revision=revision
                )

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return _copy_model(task) if task else None

    async def delete_task(self, task_id: str) -> None:
        async with self._lock:
            self._tasks.pop(task_id, None)
            self._schedule_states.pop(task_id, None)

    async def delete_runs_for_task(self, task_id: str) -> None:
        async with self._lock:
            run_ids = [
                run_id for run_id, run in self._runs.items() if run.task_id == task_id
            ]
            for run_id in run_ids:
                self._runs.pop(run_id, None)
                self._cancel_requested.discard(run_id)
            self._attempts = {
                attempt_id: attempt
                for attempt_id, attempt in self._attempts.items()
                if attempt.run_id not in set(run_ids)
            }

    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]:
        async with self._lock:
            tasks = list(self._tasks.values())
            if agent_id is not None:
                tasks = [task for task in tasks if task.agent_id == agent_id]
            if enabled is not None:
                tasks = [task for task in tasks if task.enabled is enabled]
            return [_copy_model(task) for task in self._sorted(tasks)]

    async def save_schedule_state(self, state: BackgroundScheduleState) -> None:
        async with self._lock:
            if state.task_id not in self._tasks:
                raise TaskNotFoundError(f"Task not found: {state.task_id}")
            self._schedule_states[state.task_id] = _copy_model(state)

    async def set_schedule_paused(
        self, task_id: str, paused: bool
    ) -> BackgroundScheduleState:
        async with self._lock:
            state = self._require_schedule_state(task_id)
            updated = state.model_copy(
                update={"paused": paused, "updated_at": _now()},
                deep=True,
            )
            self._schedule_states[task_id] = updated
            return _copy_model(updated)

    async def get_schedule_state(
        self, task_id: str
    ) -> BackgroundScheduleState | None:
        async with self._lock:
            state = self._schedule_states.get(task_id)
            return _copy_model(state) if state else None

    async def get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]:
        now = self._ensure_utc(now)
        due: list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]] = []
        async with self._lock:
            for task in self._sorted(self._tasks.values()):
                if len(due) >= limit:
                    break
                state = self._schedule_states.get(task.task_id)
                if not state or state.paused or not task.enabled:
                    continue
                if task.schedule.type == ScheduleType.MANUAL:
                    continue
                if state.next_due_at is None or state.next_due_at > now:
                    continue
                occurrence_id = build_occurrence_id(
                    task.schedule.type,
                    state.schedule_revision,
                    state.next_due_at,
                )
                due.append((_copy_model(task), _copy_model(state), occurrence_id))
        return due

    async def advance_schedule(
        self,
        task_id: str,
        expected_revision: int,
        occurrence_id: str,
        next_due_at: datetime | None,
    ) -> BackgroundScheduleState:
        async with self._lock:
            state = self._require_schedule_state(task_id)
            if state.schedule_revision != expected_revision:
                raise TaskStoreError("Schedule revision mismatch")
            state = state.model_copy(
                update={
                    "last_due_at": state.next_due_at,
                    "last_dispatched_at": _now(),
                    "next_due_at": self._ensure_utc(next_due_at),
                    "updated_at": _now(),
                },
                deep=True,
            )
            self._schedule_states[task_id] = state
            return _copy_model(state)

    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun:
        async with self._lock:
            if not run.occurrence_id:
                raise TaskStoreError("Scheduled runs require occurrence_id")
            state = self._require_schedule_state(run.task_id)
            if state.schedule_revision != expected_schedule_revision:
                raise TaskStoreError("Schedule revision mismatch")
            existing = self._find_run_by_occurrence(run.task_id, run.occurrence_id)
            if existing:
                return _copy_model(existing)
            created = self._create_run_with_overlap_guard_locked(run, overlap_policy)
            self._schedule_states[run.task_id] = state.model_copy(
                update={
                    "last_due_at": state.next_due_at,
                    "last_dispatched_at": _now(),
                    "next_due_at": self._ensure_utc(next_due_at),
                    "updated_at": _now(),
                },
                deep=True,
            )
            return _copy_model(created)

    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        async with self._lock:
            return _copy_model(
                self._create_run_with_overlap_guard_locked(run, overlap_policy)
            )

    async def get_run(self, run_id: str) -> BackgroundRun | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return _copy_model(run) if run else None

    async def update_run_metadata(
        self,
        run_id: str,
        patch: dict,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        async with self._lock:
            run = self._require_run(run_id)
            if worker_id is not None or lease_token is not None:
                self._verify_lease(run, worker_id, lease_token)
            metadata = {**run.metadata, **patch}
            updated = run.model_copy(update={"metadata": metadata}, deep=True)
            self._runs[run_id] = updated
            return _copy_model(updated)

    async def transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        async with self._lock:
            run = self._require_run(run_id)
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
                next_status in {RunStatus.COMPLETED, RunStatus.RETRYING, RunStatus.QUEUED}
                and run.cancel_requested_at is not None
            ):
                raise TaskStoreError("Run cancellation requested before completion")
            update = {**(patch or {}), "status": next_status}
            if next_status in TERMINAL_RUN_STATUSES:
                update.setdefault("finished_at", _now())
                update.setdefault("lease_owner", None)
                update.setdefault("lease_token", None)
                update.setdefault("lease_expires_at", None)
            updated = run.model_copy(update=update, deep=True)
            self._runs[run_id] = updated
            return _copy_model(updated)

    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]:
        async with self._lock:
            runs = list(self._runs.values())
            if task_id is not None:
                runs = [run for run in runs if run.task_id == task_id]
            if status is not None:
                runs = [run for run in runs if run.status == status]
            return [_copy_model(run) for run in self._sorted(runs, "queued_at")]

    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]:
        async with self._lock:
            runs = [run for run in self._runs.values() if run.status in ACTIVE_RUN_STATUSES]
            if task_id is not None:
                runs = [run for run in runs if run.task_id == task_id]
            return [_copy_model(run) for run in self._sorted(runs, "queued_at")]

    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        async with self._lock:
            return [_copy_model(run) for run in self._claimable_runs_locked()[:limit]]

    async def claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None:
        async with self._lock:
            claimable = self._claimable_runs_locked()
            if not claimable:
                return None
            return _copy_model(self._claim_run_locked(claimable[0], worker_id, lease_seconds))

    async def create_attempt(self, attempt: BackgroundAttempt) -> None:
        async with self._lock:
            run = self._require_run(attempt.run_id)
            self._verify_lease(run, attempt.worker_id, attempt.lease_token)
            if attempt.attempt_id in self._attempts:
                raise TaskStoreError(f"Attempt already exists: {attempt.attempt_id}")
            self._attempts[attempt.attempt_id] = _copy_model(attempt)

    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        async with self._lock:
            attempt = self._attempts.get(attempt_id)
            if not attempt:
                raise TaskStoreError(f"Attempt not found: {attempt_id}")
            run = self._require_run(attempt.run_id)
            self._verify_lease(run, worker_id, lease_token)
            updated = attempt.model_copy(update=patch, deep=True)
            self._attempts[attempt_id] = updated
            return _copy_model(updated)

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        async with self._lock:
            attempts = [
                attempt for attempt in self._attempts.values() if attempt.run_id == run_id
            ]
            attempts.sort(key=lambda item: item.attempt_number)
            return [_copy_model(attempt) for attempt in attempts]

    async def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status != RunStatus.QUEUED:
                raise RunLeaseError(f"Run {run_id} is not queued")
            claimable_ids = {item.run_id for item in self._claimable_runs_locked()}
            if run_id not in claimable_ids:
                raise RunLeaseError(f"Run {run_id} is blocked by overlap policy")
            return _copy_model(self._claim_run_locked(run, worker_id, lease_seconds))

    async def steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        async with self._lock:
            run = self._require_run(run_id)
            if not run.lease_expires_at or run.lease_expires_at > _now():
                raise RunLeaseError(f"Run {run_id} lease has not expired")
            return _copy_model(
                self._steal_expired_run_locked(run, worker_id, lease_seconds)
            )

    async def refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None:
        async with self._lock:
            run = self._require_run(run_id)
            self._verify_lease(run, worker_id, lease_token)
            self._runs[run_id] = run.model_copy(
                update={
                    "heartbeat_at": _now(),
                    "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
                },
                deep=True,
            )

    async def release_lease(
        self, run_id: str, worker_id: str, lease_token: str
    ) -> None:
        async with self._lock:
            run = self._require_run(run_id)
            self._verify_lease(run, worker_id, lease_token)
            self._runs[run_id] = run.model_copy(
                update={
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
                deep=True,
            )

    async def list_expired_leases(self, now: datetime) -> list[BackgroundRun]:
        now = self._ensure_utc(now)
        async with self._lock:
            runs = [
                run
                for run in self._runs.values()
                if run.status in {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}
                and run.lease_expires_at is not None
                and run.lease_expires_at <= now
            ]
            return [_copy_model(run) for run in self._sorted(runs, "lease_expires_at")]

    async def request_cancel(self, run_id: str) -> None:
        async with self._lock:
            run = self._require_run(run_id)
            self._cancel_requested.add(run_id)
            self._runs[run_id] = run.model_copy(
                update={"cancel_requested_at": _now()}, deep=True
            )

    async def is_cancel_requested(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._cancel_requested

    def _initial_schedule_state(
        self, task: BackgroundTaskSpec, schedule_revision: int = 1
    ) -> BackgroundScheduleState:
        return BackgroundScheduleState(
            task_id=task.task_id,
            next_due_at=initial_schedule_due(task.schedule, _now()),
            schedule_revision=schedule_revision,
        )

    def _create_run_with_overlap_guard_locked(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        if run.task_id not in self._tasks:
            raise TaskNotFoundError(f"Task not found: {run.task_id}")
        if run.run_id in self._runs:
            raise TaskStoreError(f"Run already exists: {run.run_id}")
        active = [
            item
            for item in self._runs.values()
            if item.task_id == run.task_id and item.status in ACTIVE_RUN_STATUSES
        ]
        if active and overlap_policy == OverlapPolicy.SKIP_IF_RUNNING:
            skipped = run.model_copy(
                update={"status": RunStatus.SKIPPED, "finished_at": _now()}, deep=True
            )
            self._runs[skipped.run_id] = skipped
            return skipped
        if active and overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            for item in active:
                self._cancel_requested.add(item.run_id)
                self._runs[item.run_id] = item.model_copy(
                    update={"cancel_requested_at": _now()}, deep=True
                )
        self._runs[run.run_id] = _copy_model(run)
        return run

    def _claimable_runs_locked(self) -> list[BackgroundRun]:
        queued = [
            run
            for run in self._runs.values()
            if run.status == RunStatus.QUEUED
            and (run.queued_at is None or run.queued_at <= _now())
        ]
        queued = sorted(queued, key=self._run_order_key)
        claimable: list[BackgroundRun] = []
        for run in queued:
            task = self._tasks.get(run.task_id)
            if not task:
                continue
            if task.overlap_policy == OverlapPolicy.QUEUE_NEXT:
                earlier_active = [
                    item
                    for item in self._runs.values()
                    if item.task_id == run.task_id
                    and item.run_id != run.run_id
                    and item.status in {
                        RunStatus.QUEUED,
                        RunStatus.CLAIMED,
                        RunStatus.RUNNING,
                        RunStatus.RETRYING,
                    }
                    and self._run_order_key(item) < self._run_order_key(run)
                ]
                if earlier_active:
                    continue
            claimable.append(run)
        return claimable

    def _claim_run_locked(
        self, run: BackgroundRun, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        token = uuid4().hex
        claimed = run.model_copy(
            update={
                "status": RunStatus.CLAIMED,
                "lease_owner": worker_id,
                "lease_token": token,
                "lease_generation": run.lease_generation + 1,
                "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
                "heartbeat_at": _now(),
                "claimed_at": _now(),
            },
            deep=True,
        )
        self._runs[run.run_id] = claimed
        return claimed

    def _steal_expired_run_locked(
        self, run: BackgroundRun, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        token = uuid4().hex
        stolen = run.model_copy(
            update={
                "lease_owner": worker_id,
                "lease_token": token,
                "lease_generation": run.lease_generation + 1,
                "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
                "heartbeat_at": _now(),
            },
            deep=True,
        )
        self._runs[run.run_id] = stolen
        return stolen

    def _require_run(self, run_id: str) -> BackgroundRun:
        run = self._runs.get(run_id)
        if not run:
            raise RunNotFoundError(f"Run not found: {run_id}")
        return run

    def _require_schedule_state(self, task_id: str) -> BackgroundScheduleState:
        state = self._schedule_states.get(task_id)
        if not state:
            raise TaskNotFoundError(f"Schedule state not found for task: {task_id}")
        return state

    def _find_run_by_occurrence(
        self, task_id: str, occurrence_id: str
    ) -> BackgroundRun | None:
        for run in self._runs.values():
            if run.task_id == task_id and run.occurrence_id == occurrence_id:
                return run
        return None

    def _verify_lease(
        self, run: BackgroundRun, worker_id: str | None, lease_token: str | None
    ) -> None:
        self._verify_lease_identity(run, worker_id, lease_token)
        if run.lease_expires_at is not None and run.lease_expires_at <= _now():
            raise RunLeaseError("Run lease has expired")

    @staticmethod
    def _verify_lease_identity(
        run: BackgroundRun, worker_id: str | None, lease_token: str | None
    ) -> None:
        if not worker_id or not lease_token:
            raise RunLeaseError("worker_id and lease_token are required")
        if run.lease_owner != worker_id or run.lease_token != lease_token:
            raise RunLeaseError("Run lease token mismatch")

    @staticmethod
    def _ensure_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _sorted(items, field: str = "created_at"):
        return sorted(items, key=lambda item: getattr(item, field, None) or utc_now())

    @staticmethod
    def _run_order_key(run: BackgroundRun) -> tuple[datetime, datetime, str]:
        queued_at = run.queued_at or run.triggered_at
        return (queued_at, run.triggered_at, run.run_id)
