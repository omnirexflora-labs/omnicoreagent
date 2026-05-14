"""Expired lease recovery for background runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from omnicoreagent.background.errors import (
    RunCancellationRequestedError,
    RunLeaseError,
)
from omnicoreagent.background.models import (
    AttemptReason,
    AttemptStatus,
    BackgroundRun,
    RunStatus,
    utc_now,
)
from omnicoreagent.background.run_helpers import release_lease_patch
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.transitions import BackgroundRunTransitions


class BackgroundRunRecovery:
    """Recovers runs whose worker lease expired before terminal completion."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        transitions: BackgroundRunTransitions,
        worker_id: str | Callable[[], str],
        lease_seconds: int | Callable[[], int],
        emit_run: Callable[..., Awaitable[None]],
    ) -> None:
        self.task_store = task_store
        self.transitions = transitions
        self._worker_id = worker_id if callable(worker_id) else lambda: worker_id
        self._lease_seconds = (
            lease_seconds if callable(lease_seconds) else lambda: lease_seconds
        )
        self.emit_run = emit_run

    @property
    def worker_id(self) -> str:
        return self._worker_id()

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds()

    async def recover_expired_runs(self) -> None:
        expired = await self.task_store.list_expired_leases(utc_now())
        for run in expired:
            try:
                await self.recover_expired_run(run)
            except (RunLeaseError, RunCancellationRequestedError):
                continue

    async def recover_expired_run(self, run: BackgroundRun) -> None:
        stolen = await self.task_store.steal_expired_run(
            run.run_id, self.worker_id, self.lease_seconds
        )
        if await self.task_store.is_cancel_requested(stolen.run_id):
            await self.transitions.mark_terminal(
                stolen, RunStatus.CANCELLED, "cancelled"
            )
            return

        await self.fail_abandoned_attempts(stolen)
        task = await self.task_store.get_task(stolen.task_id)
        if not task:
            await self.transitions.mark_terminal(stolen, RunStatus.FAILED, "task missing")
            return

        if stolen.status == RunStatus.CLAIMED:
            recovered = await self.transitions.transition_or_cancel_without_attempt(
                run=stolen,
                expected={RunStatus.CLAIMED},
                next_status=RunStatus.QUEUED,
                patch=release_lease_patch(),
            )
            if recovered is None:
                return
            await self.emit_run("background_run_recovered", recovered)
            return

        can_retry = stolen.attempt <= task.retry_policy.max_retries
        if can_retry:
            await self.requeue_retryable_run(stolen)
            return

        await self.transitions.mark_terminal(stolen, RunStatus.FAILED, "lease expired")

    async def fail_abandoned_attempts(self, run: BackgroundRun) -> None:
        attempts = await self.task_store.list_attempts(run.run_id)
        running = [item for item in attempts if item.status == AttemptStatus.RUNNING]
        for attempt in running:
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {
                    "status": AttemptStatus.FAILED,
                    "reason": AttemptReason.LEASE_EXPIRED,
                    "finished_at": utc_now(),
                    "error": "lease expired",
                },
                self.worker_id,
                run.lease_token,
            )

    async def requeue_retryable_run(self, run: BackgroundRun) -> None:
        retrying = run
        if run.status == RunStatus.RUNNING:
            retrying = await self.transitions.transition_or_cancel_without_attempt(
                run=run,
                expected={RunStatus.RUNNING},
                next_status=RunStatus.RETRYING,
                patch={"error": "lease expired"},
            )
            if retrying is None:
                return
        recovered = await self.transitions.transition_or_cancel_without_attempt(
            run=retrying,
            expected={RunStatus.RETRYING},
            next_status=RunStatus.QUEUED,
            patch=release_lease_patch(),
        )
        if recovered is None:
            return
        await self.emit_run("background_run_recovered", recovered)
