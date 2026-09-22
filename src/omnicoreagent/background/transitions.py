"""Lease-aware run transition helpers for background execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.background.errors import RunCancellationRequestedError
from omnicoreagent.background.models import (
    AttemptStatus,
    BackgroundAttempt,
    BackgroundRun,
    RunStatus,
    utc_now,
)
from omnicoreagent.background.store.base import AbstractTaskStore


class BackgroundRunTransitions:
    """Centralized run transition policy for workers holding a lease."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        worker_id: str | Callable[[], str],
        lease_seconds: int | Callable[[], int],
        emit_run: Callable[..., Awaitable[None]],
    ) -> None:
        self.task_store = task_store
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

    async def refresh_run_lease(self, run_id: str, lease_token: str) -> bool:
        worker_id = self.worker_id
        lease_seconds = self.lease_seconds
        try:
            await self.task_store.refresh_lease(
                run_id, worker_id, lease_token, lease_seconds
            )
            return True
        except Exception:
            return False

    async def mark_terminal(
        self, run: BackgroundRun, status: RunStatus, error: str | None
    ) -> None:
        latest = await self.task_store.get_run(run.run_id)
        if not latest:
            return
        if latest.lease_token is not None:
            if not await self.refresh_run_lease(latest.run_id, latest.lease_token):
                return
            latest = await self.task_store.get_run(run.run_id) or latest
        terminal = await self.task_store.transition_run(
            latest.run_id,
            {latest.status},
            status,
            {"error": error},
            self.worker_id,
            latest.lease_token,
        )
        await self.emit_run(f"background_run_{status.value}", terminal)

    async def mark_attempt_cancelled(
        self, attempt: BackgroundAttempt, run: BackgroundRun
    ) -> None:
        await self.task_store.update_attempt(
            attempt.attempt_id,
            {
                "status": AttemptStatus.CANCELLED,
                "finished_at": utc_now(),
                "error": "cancelled",
            },
            self.worker_id,
            run.lease_token,
        )

    async def cancel_if_requested(
        self, run: BackgroundRun, attempt: BackgroundAttempt
    ) -> bool:
        if not await self.task_store.is_cancel_requested(run.run_id):
            return False
        if run.lease_token is not None:
            if not await self.refresh_run_lease(run.run_id, run.lease_token):
                recovered = await self._recover_expired_owned_cancelled_run(run)
                if recovered is None:
                    return True
                run = recovered
        await self.mark_attempt_cancelled(attempt, run)
        await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
        return True

    async def _recover_expired_owned_cancelled_run(
        self, run: BackgroundRun
    ) -> BackgroundRun | None:
        latest = await self.task_store.get_run(run.run_id)
        if (
            latest is None
            or latest.lease_owner != self.worker_id
            or latest.status
            not in {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}
        ):
            return None
        try:
            return await self.task_store.steal_expired_run(
                latest.run_id,
                self.worker_id,
                self.lease_seconds,
            )
        except Exception:
            return None

    async def transition_or_cancel(
        self,
        *,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict[str, Any] | None = None,
    ) -> BackgroundRun | None:
        try:
            return await self.task_store.transition_run(
                run.run_id,
                expected,
                next_status,
                patch,
                self.worker_id,
                run.lease_token,
            )
        except RunCancellationRequestedError:
            if await self.cancel_if_requested(run, attempt):
                return None
            raise

    async def transition_or_cancel_without_attempt(
        self,
        *,
        run: BackgroundRun,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict[str, Any] | None = None,
    ) -> BackgroundRun | None:
        try:
            return await self.task_store.transition_run(
                run.run_id,
                expected,
                next_status,
                patch,
                self.worker_id,
                run.lease_token,
            )
        except RunCancellationRequestedError:
            latest = await self.task_store.get_run(run.run_id)
            await self.mark_terminal(latest or run, RunStatus.CANCELLED, "cancelled")
            return None

    async def mark_completed_if_not_cancelled(
        self,
        *,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        result_preview: str | None,
    ) -> None:
        latest = await self.task_store.get_run(run.run_id)
        if not latest:
            return
        if latest.cancel_requested_at is not None:
            await self.mark_attempt_cancelled(attempt, latest)
            await self.mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
            return
        try:
            completed = await self.task_store.transition_run(
                latest.run_id,
                {RunStatus.RUNNING},
                RunStatus.COMPLETED,
                {"result_preview": result_preview},
                self.worker_id,
                latest.lease_token,
            )
        except RunCancellationRequestedError:
            if await self.cancel_if_requested(run, attempt):
                return
            raise
        if completed.cancel_requested_at is not None:
            await self.mark_attempt_cancelled(attempt, completed)
            await self.mark_terminal(completed, RunStatus.CANCELLED, "cancelled")
            return
        await self.emit_run("background_run_completed", completed)
