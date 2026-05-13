"""Run supervision service for background execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from omnicoreagent.core.events.base import reset_event_run_id, set_event_run_id
from omnicoreagent.background.agent_specs import resolve_agent
from omnicoreagent.background.errors import RunLeaseError, RunNotFoundError
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
            except RunLeaseError:
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
            recovered = await self.task_store.transition_run(
                stolen.run_id,
                {RunStatus.CLAIMED},
                RunStatus.QUEUED,
                release_lease_patch(),
                self.worker_id,
                stolen.lease_token,
            )
            await self.emit_run("background_run_recovered", recovered)
            return

        can_retry = stolen.attempt <= task.retry_policy.max_retries
        if can_retry:
            retrying = stolen
            if stolen.status == RunStatus.RUNNING:
                retrying = await self.task_store.transition_run(
                    stolen.run_id,
                    {RunStatus.RUNNING},
                    RunStatus.RETRYING,
                    {"error": "lease expired"},
                    self.worker_id,
                    stolen.lease_token,
                )
            recovered = await self.task_store.transition_run(
                retrying.run_id,
                {RunStatus.RETRYING},
                RunStatus.QUEUED,
                release_lease_patch(),
                self.worker_id,
                retrying.lease_token,
            )
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
        released = await self.task_store.transition_run(
            latest.run_id,
            {RunStatus.CLAIMED},
            RunStatus.QUEUED,
            release_lease_patch(),
            self.worker_id,
            latest.lease_token,
        )
        await self.emit_run("background_run_queued", released)

    async def run_claimed(self, claimed: BackgroundRun) -> None:
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
            await self._mark_attempt_cancelled(attempt, run)
            await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
            return
        heartbeat_task = asyncio.create_task(
            self.heartbeat_until_finished(run.run_id, run.lease_token)
        )
        await self.emit_run("background_run_claimed", claimed)
        await self.emit_run("background_run_started", run)

        try:
            if await self.task_store.is_cancel_requested(run.run_id):
                await self._mark_attempt_cancelled(attempt, run)
                await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            query = build_run_context(run)
            agent_task = asyncio.create_task(
                self.run_agent_with_event_context(
                    agent=agent,
                    query=query,
                    run=run,
                    timeout_seconds=task.timeout_seconds,
                )
            )
            self.active_agent_tasks[run.run_id] = agent_task
            result = await agent_task
        except asyncio.CancelledError:
            try:
                if await self.task_store.is_cancel_requested(run.run_id):
                    await self._mark_attempt_cancelled(attempt, run)
                    await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                    return
                await self.handle_attempt_failure(
                    task, run, attempt, "exception", RuntimeError("worker shutdown")
                )
            finally:
                self.active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self.drain_cancelled_task(heartbeat_task)
            raise
        except asyncio.TimeoutError as exc:
            try:
                await self.handle_attempt_failure(task, run, attempt, "timeout", exc)
            finally:
                self.active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self.drain_cancelled_task(heartbeat_task)
            return
        except Exception as exc:
            try:
                await self.handle_attempt_failure(task, run, attempt, "exception", exc)
            finally:
                self.active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self.drain_cancelled_task(heartbeat_task)
            return

        try:
            preview = result_preview(result)
            if await self.task_store.is_cancel_requested(run.run_id):
                if run.lease_token is not None:
                    if not await self.refresh_run_lease(run.run_id, run.lease_token):
                        return
                await self._mark_attempt_cancelled(attempt, run)
                await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            if run.lease_token is not None:
                if not await self.refresh_run_lease(run.run_id, run.lease_token):
                    return
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"status": AttemptStatus.COMPLETED, "finished_at": utc_now()},
                self.worker_id,
                run.lease_token,
            )
            if await self.task_store.is_cancel_requested(run.run_id):
                await self._mark_attempt_cancelled(attempt, run)
                await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            if run.lease_token is not None:
                if not await self.refresh_run_lease(run.run_id, run.lease_token):
                    return
            if await self.task_store.is_cancel_requested(run.run_id):
                await self._mark_attempt_cancelled(attempt, run)
                await self.mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            completed = await self.task_store.transition_run(
                run.run_id,
                {RunStatus.RUNNING},
                RunStatus.COMPLETED,
                {"result_preview": preview},
                self.worker_id,
                run.lease_token,
            )
            await self.emit_run("background_run_completed", completed)
        finally:
            self.active_agent_tasks.pop(run.run_id, None)
            heartbeat_task.cancel()
            await self.drain_cancelled_task(heartbeat_task)

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
            retrying = await self.task_store.transition_run(
                run.run_id,
                {RunStatus.RUNNING},
                RunStatus.RETRYING,
                {"error": str(exc)},
                self.worker_id,
                run.lease_token,
            )
            await self.emit_run("background_run_retrying", retrying)
            queued = await self.task_store.transition_run(
                retrying.run_id,
                {RunStatus.RETRYING},
                RunStatus.QUEUED,
                {
                    **release_lease_patch(),
                    "queued_at": utc_now() + timedelta(seconds=retry_delay),
                },
                self.worker_id,
                retrying.lease_token,
            )
            await self.emit_run("background_run_queued", queued)
            return
        terminal = RunStatus.TIMEOUT if reason == "timeout" else RunStatus.FAILED
        await self.mark_terminal(run, terminal, str(exc))

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
        try:
            await self.task_store.refresh_lease(
                run_id, self.worker_id, lease_token, self.lease_seconds
            )
            return True
        except Exception:
            return False

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

    async def _mark_attempt_cancelled(
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
