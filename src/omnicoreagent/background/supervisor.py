"""Run supervision service for background execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from omnicoreagent.core.events.base import reset_event_run_id, set_event_run_id
from omnicoreagent.background.agent_specs import resolve_agent
from omnicoreagent.background.errors import (
    RunCancellationRequestedError,
    RunLeaseError,
    RunNotFoundError,
)
from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.models import (
    TERMINAL_RUN_STATUSES,
    AttemptReason,
    AttemptStatus,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    RunStatus,
    utc_now,
)
from omnicoreagent.background.run_helpers import (
    build_run_context,
    is_run_due,
    release_lease_patch,
    result_preview,
    retry_delay_seconds,
    run_until_terminal_sleep_seconds,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.transitions import BackgroundRunTransitions


@dataclass(slots=True)
class _RunningAttempt:
    task: BackgroundTaskSpec
    agent: Any
    run: BackgroundRun
    attempt: BackgroundAttempt
    heartbeat_task: asyncio.Task


_ATTEMPT_ALREADY_TERMINAL = object()


class BackgroundSupervisor:
    """Claims, executes, retries, cancels, and recovers background runs."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        agents: dict[str, Any],
        worker_id: str,
        lease_seconds: int,
        memory_router: Any = None,
        event_router: Any = None,
        event_log: BackgroundEventLog,
        emit_run: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.task_store = task_store
        self.agents = agents
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.memory_router = memory_router
        self.event_router = event_router
        self.event_log = event_log
        self._emit_run = emit_run
        self.transitions = BackgroundRunTransitions(
            task_store=self.task_store,
            worker_id=lambda: self.worker_id,
            lease_seconds=lambda: self.lease_seconds,
            emit_run=self.emit_run,
        )
        self.inline_execution_tasks: dict[str, asyncio.Task] = {}
        self.active_agent_tasks: dict[str, asyncio.Task] = {}

    async def emit_run(
        self, event_name: str, run: BackgroundRun, **extra_payload: Any
    ) -> None:
        if self._emit_run is not None:
            await self._emit_run(event_name, run, **extra_payload)
            return
        await self.event_log.emit_run(event_name, run, **extra_payload)

    async def run_until_terminal(
        self,
        run_id: str,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ) -> BackgroundRun:
        deadline = (
            asyncio.get_running_loop().time() + timeout_seconds
            if timeout_seconds and timeout_seconds > 0
            else None
        )
        while True:
            latest = await self.task_store.get_run(run_id)
            if not latest:
                raise RunNotFoundError(f"Run not found: {run_id}")
            if latest.status in TERMINAL_RUN_STATUSES:
                return latest

            if latest.status == RunStatus.QUEUED and is_run_due(latest):
                execution_task = self.get_or_start_inline_execution_task(run_id)
                if deadline is None:
                    executed = await execution_task
                    if not executed:
                        return await self.task_store.get_run(run_id) or latest
                    continue
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return latest
                try:
                    executed = await asyncio.wait_for(
                        asyncio.shield(execution_task),
                        timeout=remaining,
                    )
                    if not executed:
                        latest = await self.task_store.get_run(run_id) or latest
                    else:
                        continue
                except asyncio.TimeoutError:
                    return await self.task_store.get_run(run_id) or latest

            if deadline is None:
                return latest

            if asyncio.get_running_loop().time() >= deadline:
                return latest

            await asyncio.sleep(
                run_until_terminal_sleep_seconds(
                    latest,
                    deadline,
                    poll_interval_seconds,
                )
            )

    def get_or_start_inline_execution_task(self, run_id: str) -> asyncio.Task:
        existing = self.inline_execution_tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self.execute_run(run_id))
        self.inline_execution_tasks[run_id] = task

        def _forget(completed: asyncio.Task) -> None:
            if self.inline_execution_tasks.get(run_id) is completed:
                self.inline_execution_tasks.pop(run_id, None)

        task.add_done_callback(_forget)
        return task

    async def cancel_inline_execution_tasks(self) -> None:
        pending = [
            task for task in self.inline_execution_tasks.values() if not task.done()
        ]
        if not pending:
            self.inline_execution_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.inline_execution_tasks.clear()

    async def execute_one(self) -> bool:
        claimed = await self.task_store.claim_next_run(
            self.worker_id, self.lease_seconds
        )
        if not claimed:
            return False
        try:
            await self.run_claimed(claimed)
        except asyncio.CancelledError:
            await self.release_claimed_before_start(claimed.run_id)
            raise
        return True

    async def execute_run(self, run_id: str) -> bool:
        latest = await self.task_store.get_run(run_id)
        if (
            not latest
            or latest.status != RunStatus.QUEUED
            or (latest.queued_at is not None and latest.queued_at > utc_now())
        ):
            return False
        claimable = await self.task_store.list_claimable_runs(limit=10_000)
        if latest.run_id not in {run.run_id for run in claimable}:
            return False
        try:
            claimed = await self.task_store.claim_run(
                run_id, self.worker_id, self.lease_seconds
            )
        except RunLeaseError:
            return False
        try:
            await self.run_claimed(claimed)
        except asyncio.CancelledError:
            await self.release_claimed_before_start(claimed.run_id)
            raise
        return True

    async def cancel_run(self, run_id: str) -> None:
        run = await self.task_store.get_run(run_id)
        if not run:
            raise RunNotFoundError(f"Run not found: {run_id}")
        await self.task_store.request_cancel(run_id)
        latest = await self.task_store.get_run(run_id)
        if not latest:
            return
        if latest.status == RunStatus.QUEUED:
            await self.mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif (
            latest.status == RunStatus.CLAIMED
            and latest.lease_owner == self.worker_id
        ):
            await self.mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif latest.status == RunStatus.RUNNING and latest.lease_owner == self.worker_id:
            active_task = self.active_agent_tasks.get(run_id)
            if active_task is not None and not active_task.done():
                active_task.cancel()

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
            await self.mark_terminal(stolen, RunStatus.CANCELLED, "cancelled")
            return

        attempts = await self.task_store.list_attempts(stolen.run_id)
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
                stolen.lease_token,
            )
        task = await self.task_store.get_task(stolen.task_id)
        if not task:
            await self.mark_terminal(stolen, RunStatus.FAILED, "task missing")
            return

        if stolen.status == RunStatus.CLAIMED:
            recovered = await self.transition_or_cancel_without_attempt(
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
            retrying = stolen
            if stolen.status == RunStatus.RUNNING:
                retrying = await self.transition_or_cancel_without_attempt(
                    run=stolen,
                    expected={RunStatus.RUNNING},
                    next_status=RunStatus.RETRYING,
                    patch={"error": "lease expired"},
                )
                if retrying is None:
                    return
            recovered = await self.transition_or_cancel_without_attempt(
                run=retrying,
                expected={RunStatus.RETRYING},
                next_status=RunStatus.QUEUED,
                patch=release_lease_patch(),
            )
            if recovered is None:
                return
            await self.emit_run("background_run_recovered", recovered)
            return

        await self.mark_terminal(stolen, RunStatus.FAILED, "lease expired")

    async def release_claimed_before_start(self, run_id: str) -> None:
        latest = await self.task_store.get_run(run_id)
        if (
            not latest
            or latest.status != RunStatus.CLAIMED
            or latest.lease_owner != self.worker_id
            or latest.lease_token is None
        ):
            return
        released = await self.transition_or_cancel_without_attempt(
            run=latest,
            expected={RunStatus.CLAIMED},
            next_status=RunStatus.QUEUED,
            patch=release_lease_patch(),
        )
        if released is None:
            return
        await self.emit_run("background_run_queued", released)

    async def run_claimed(self, claimed: BackgroundRun) -> None:
        target = await self.resolve_claimed_target(claimed)
        if target is None:
            return
        task, agent = target

        running = await self.start_claimed_attempt(claimed, task, agent)
        if running is None:
            return

        try:
            result = await self.execute_agent_attempt(running)
        except asyncio.CancelledError:
            try:
                if await self.task_store.is_cancel_requested(running.run.run_id):
                    await self.mark_attempt_cancelled(running.attempt, running.run)
                    await self.mark_terminal(
                        running.run, RunStatus.CANCELLED, "cancelled"
                    )
                    return
                await self.handle_attempt_failure(
                    running.task,
                    running.run,
                    running.attempt,
                    "exception",
                    RuntimeError("worker shutdown"),
                )
            finally:
                await self.cleanup_running_attempt(running)
            raise
        except asyncio.TimeoutError as exc:
            try:
                await self.handle_attempt_failure(
                    running.task, running.run, running.attempt, "timeout", exc
                )
            finally:
                await self.cleanup_running_attempt(running)
            return
        except Exception as exc:
            try:
                await self.handle_attempt_failure(
                    running.task, running.run, running.attempt, "exception", exc
                )
            finally:
                await self.cleanup_running_attempt(running)
            return

        try:
            if result is _ATTEMPT_ALREADY_TERMINAL:
                return
            await self.complete_successful_attempt(running, result)
        finally:
            await self.cleanup_running_attempt(running)

    async def resolve_claimed_target(
        self, claimed: BackgroundRun
    ) -> tuple[BackgroundTaskSpec, Any] | None:
        task = await self.task_store.get_task(claimed.task_id)
        if not task:
            await self.mark_terminal(claimed, RunStatus.FAILED, "task missing")
            return
        agent = await resolve_agent(
            agent_id=claimed.agent_id,
            agents=self.agents,
            task_store=self.task_store,
            memory_router=self.memory_router,
            event_router=self.event_router,
        )
        if agent is None:
            await self.mark_terminal(claimed, RunStatus.FAILED, "agent missing")
            return
        if await self.task_store.is_cancel_requested(claimed.run_id):
            await self.mark_terminal(claimed, RunStatus.CANCELLED, "cancelled")
            return
        return task, agent

    async def start_claimed_attempt(
        self, claimed: BackgroundRun, task: BackgroundTaskSpec, agent: Any
    ) -> _RunningAttempt | None:
        if claimed.lease_token is not None:
            if not await self.refresh_run_lease(claimed.run_id, claimed.lease_token):
                return
            claimed = await self.task_store.get_run(claimed.run_id) or claimed

        attempt_number = claimed.attempt + 1
        run = await self.task_store.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            {"started_at": utc_now(), "attempt": attempt_number},
            self.worker_id,
            claimed.lease_token,
        )
        if run.lease_token is not None:
            if not await self.refresh_run_lease(run.run_id, run.lease_token):
                return
            run = await self.task_store.get_run(run.run_id) or run
        attempt = BackgroundAttempt(
            run_id=run.run_id,
            attempt_number=attempt_number,
            reason=AttemptReason.INITIAL if attempt_number == 1 else AttemptReason.RETRY,
            worker_id=self.worker_id,
            lease_token=run.lease_token,
        )
        await self.task_store.create_attempt(attempt)
        if await self.task_store.is_cancel_requested(run.run_id):
            await self.mark_attempt_cancelled(attempt, run)
            await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
            return
        heartbeat_task = asyncio.create_task(
            self.heartbeat_until_finished(run.run_id, run.lease_token)
        )
        await self.emit_run("background_run_claimed", claimed)
        await self.emit_run("background_run_started", run)
        return _RunningAttempt(
            task=task,
            agent=agent,
            run=run,
            attempt=attempt,
            heartbeat_task=heartbeat_task,
        )

    async def execute_agent_attempt(self, running: _RunningAttempt) -> Any:
        if await self.task_store.is_cancel_requested(running.run.run_id):
            await self.mark_attempt_cancelled(running.attempt, running.run)
            await self.mark_terminal(running.run, RunStatus.CANCELLED, "cancelled")
            return _ATTEMPT_ALREADY_TERMINAL
        query = build_run_context(running.run)
        agent_task = asyncio.create_task(
            self.run_agent_with_event_context(
                agent=running.agent,
                query=query,
                run=running.run,
                timeout_seconds=running.task.timeout_seconds,
            )
        )
        self.active_agent_tasks[running.run.run_id] = agent_task
        return await agent_task

    async def complete_successful_attempt(
        self, running: _RunningAttempt, result: Any
    ) -> None:
        preview = result_preview(result)
        if await self.cancel_if_requested(running.run, running.attempt):
            return
        if running.run.lease_token is not None:
            if not await self.refresh_run_lease(
                running.run.run_id, running.run.lease_token
            ):
                return
        await self.task_store.update_attempt(
            running.attempt.attempt_id,
            {"status": AttemptStatus.COMPLETED, "finished_at": utc_now()},
            self.worker_id,
            running.run.lease_token,
        )
        if await self.cancel_if_requested(running.run, running.attempt):
            return
        if running.run.lease_token is not None:
            if not await self.refresh_run_lease(
                running.run.run_id, running.run.lease_token
            ):
                return
        if await self.cancel_if_requested(running.run, running.attempt):
            return
        await self.mark_completed_if_not_cancelled(running, preview)

    async def transition_or_cancel(
        self,
        *,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict[str, Any] | None = None,
    ) -> BackgroundRun | None:
        return await self.transitions.transition_or_cancel(
            run=run,
            attempt=attempt,
            expected=expected,
            next_status=next_status,
            patch=patch,
        )

    async def transition_or_cancel_without_attempt(
        self,
        *,
        run: BackgroundRun,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict[str, Any] | None = None,
    ) -> BackgroundRun | None:
        return await self.transitions.transition_or_cancel_without_attempt(
            run=run,
            expected=expected,
            next_status=next_status,
            patch=patch,
        )

    async def mark_completed_if_not_cancelled(
        self, running: _RunningAttempt, preview: str | None
    ) -> None:
        await self.transitions.mark_completed_if_not_cancelled(
            run=running.run,
            attempt=running.attempt,
            result_preview=preview,
        )

    async def cancel_if_requested(
        self, run: BackgroundRun, attempt: BackgroundAttempt
    ) -> bool:
        return await self.transitions.cancel_if_requested(run, attempt)

    async def cleanup_running_attempt(self, running: _RunningAttempt) -> None:
        self.active_agent_tasks.pop(running.run.run_id, None)
        running.heartbeat_task.cancel()
        await self.drain_cancelled_task(running.heartbeat_task)

    async def run_agent_with_event_context(
        self,
        *,
        agent: Any,
        query: str,
        run: BackgroundRun,
        timeout_seconds: int | None,
    ) -> Any:
        token = set_event_run_id(run.run_id)
        try:
            coro = agent.run(query=query, session_id=run.session_id)
            return (
                await asyncio.wait_for(coro, timeout=timeout_seconds)
                if timeout_seconds
                else await coro
            )
        finally:
            reset_event_run_id(token)

    async def handle_attempt_failure(
        self,
        task: BackgroundTaskSpec,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        reason: str,
        exc: BaseException,
    ) -> None:
        status = AttemptStatus.TIMEOUT if reason == "timeout" else AttemptStatus.FAILED
        if run.lease_token is not None:
            if not await self.refresh_run_lease(run.run_id, run.lease_token):
                return
        await self.task_store.update_attempt(
            attempt.attempt_id,
            {"status": status, "finished_at": utc_now(), "error": str(exc)},
            self.worker_id,
            run.lease_token,
        )
        if await self.cancel_if_requested(run, attempt):
            return
        can_retry = (
            reason in task.retry_policy.retry_on
            and run.attempt <= task.retry_policy.max_retries
        )
        if can_retry:
            retry_delay = retry_delay_seconds(task, run.attempt)
            if run.lease_token is not None:
                if not await self.refresh_run_lease(run.run_id, run.lease_token):
                    return
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"retry_delay_seconds": retry_delay},
                self.worker_id,
                run.lease_token,
            )
            if run.lease_token is not None:
                if not await self.refresh_run_lease(run.run_id, run.lease_token):
                    return
            if await self.cancel_if_requested(run, attempt):
                return
            retrying = await self.transition_or_cancel(
                run=run,
                attempt=attempt,
                expected={RunStatus.RUNNING},
                next_status=RunStatus.RETRYING,
                patch={"error": str(exc)},
            )
            if retrying is None:
                return
            await self.emit_run("background_run_retrying", retrying)
            if await self.cancel_if_requested(retrying, attempt):
                return
            queued = await self.transition_or_cancel(
                run=retrying,
                attempt=attempt,
                expected={RunStatus.RETRYING},
                next_status=RunStatus.QUEUED,
                patch={
                    **release_lease_patch(),
                    "queued_at": utc_now() + timedelta(seconds=retry_delay),
                },
            )
            if queued is None:
                return
            await self.emit_run("background_run_queued", queued)
            return
        terminal = RunStatus.TIMEOUT if reason == "timeout" else RunStatus.FAILED
        await self.mark_terminal(run, terminal, str(exc))

    async def mark_terminal(
        self, run: BackgroundRun, status: RunStatus, error: str | None
    ) -> None:
        await self.transitions.mark_terminal(run, status, error)

    async def heartbeat_until_finished(
        self, run_id: str, lease_token: str | None
    ) -> None:
        if lease_token is None:
            return
        interval = max(0.01, self.lease_seconds / 4)
        if not await self.refresh_run_lease(run_id, lease_token):
            return
        while True:
            await asyncio.sleep(interval)
            if not await self.refresh_run_lease(run_id, lease_token):
                return
            try:
                latest = await self.task_store.get_run(run_id)
                if latest and latest.status in {RunStatus.RUNNING, RunStatus.RETRYING}:
                    await self.emit_run("background_run_heartbeat", latest)
            except Exception:
                continue

    async def refresh_run_lease(self, run_id: str, lease_token: str) -> bool:
        return await self.transitions.refresh_run_lease(run_id, lease_token)

    async def drain_cancelled_task(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def cancel_active_agent_tasks(self) -> None:
        pending = [task for task in self.active_agent_tasks.values() if not task.done()]
        if not pending:
            self.active_agent_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.active_agent_tasks.clear()

    async def mark_attempt_cancelled(
        self, attempt: BackgroundAttempt, run: BackgroundRun
    ) -> None:
        await self.transitions.mark_attempt_cancelled(attempt, run)
