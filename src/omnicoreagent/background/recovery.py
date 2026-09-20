"""Expired lease recovery for background runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

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
from omnicoreagent.background.run_helpers import release_lease_patch, retries_spent
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.transitions import BackgroundRunTransitions
from omnicoreagent.core.runs import lease_expired, not_resumable


class BackgroundRunRecovery:
    """Recovers runs whose worker lease expired before terminal completion.

    ``checkpoint`` returns the agent's own durable record of a run (its step,
    tool calls, heartbeat), or None when the agent keeps none. With one, a
    lost run is not a failed one: it goes back to the queue and the next
    attempt continues it from that record, spending no retry.
    """

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        transitions: BackgroundRunTransitions,
        worker_id: str | Callable[[], str],
        lease_seconds: int | Callable[[], int],
        emit_run: Callable[..., Awaitable[None]],
        checkpoint: Callable[[BackgroundRun], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self.task_store = task_store
        self.transitions = transitions
        self._worker_id = worker_id if callable(worker_id) else lambda: worker_id
        self._lease_seconds = (
            lease_seconds if callable(lease_seconds) else lambda: lease_seconds
        )
        self.emit_run = emit_run
        self.checkpoint = checkpoint

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

        record = await self.checkpoint_of(stolen)
        if record is not None and record.get("status") == "running" and not lease_expired(record):
            # The agent's heartbeat is still fresh: the run may be alive in a
            # process whose worker merely stopped refreshing the lease. Hold it
            # under the stolen lease and decide again when that expires.
            return
        resumable = record is not None and not_resumable(record, stolen.run_id) is None

        await self.close_abandoned_attempts(stolen, resumable=resumable)
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

        if resumable:
            await self.requeue_run(stolen, resumed=True)
            return

        attempts = await self.task_store.list_attempts(stolen.run_id)
        if retries_spent(attempts) <= task.retry_policy.max_retries:
            await self.requeue_run(stolen, resumed=False)
            return

        await self.transitions.mark_terminal(stolen, RunStatus.FAILED, "lease expired")

    async def checkpoint_of(self, run: BackgroundRun) -> dict[str, Any] | None:
        if self.checkpoint is None or run.status != RunStatus.RUNNING:
            return None
        return await self.checkpoint(run)

    async def close_abandoned_attempts(
        self, run: BackgroundRun, *, resumable: bool
    ) -> None:
        """Close the attempts the lost worker left running: interrupted when
        the run continues from its checkpoint, failed otherwise."""
        attempts = await self.task_store.list_attempts(run.run_id)
        running = [item for item in attempts if item.status == AttemptStatus.RUNNING]
        for attempt in running:
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {
                    "status": AttemptStatus.INTERRUPTED if resumable else AttemptStatus.FAILED,
                    "reason": AttemptReason.LEASE_EXPIRED,
                    "finished_at": utc_now(),
                    "error": None if resumable else "lease expired",
                },
                self.worker_id,
                run.lease_token,
            )

    async def fail_abandoned_attempts(self, run: BackgroundRun) -> None:
        await self.close_abandoned_attempts(run, resumable=False)

    async def requeue_run(self, run: BackgroundRun, *, resumed: bool) -> None:
        """Queue a lost RUNNING run again. A resumed run keeps a clean error
        and one more attempt to finish in; a retried one records why."""
        retrying = run
        if run.status == RunStatus.RUNNING:
            patch = (
                {"max_attempts": run.max_attempts + 1}
                if resumed
                else {"error": "lease expired"}
            )
            retrying = await self.transitions.transition_or_cancel_without_attempt(
                run=run,
                expected={RunStatus.RUNNING},
                next_status=RunStatus.RETRYING,
                patch=patch,
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
        await self.emit_run("background_run_recovered", recovered, resumed=resumed)

    async def requeue_retryable_run(self, run: BackgroundRun) -> None:
        await self.requeue_run(run, resumed=False)
