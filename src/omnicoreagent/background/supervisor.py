"""Run supervision service for background execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import inspect
from inspect import Parameter
from typing import Any

from omnicoreagent.background.agent_specs import resolve_agent
from omnicoreagent.background.errors import RunLeaseError, RunNotFoundError
from omnicoreagent.core.logging import logger
from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.models import (
    SETTLED_RUN_STATUSES,
    AttemptReason,
    AttemptStatus,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    RunStatus,
    WAITING_RUN_STATUSES,
    utc_now,
)
from omnicoreagent.background.recovery import BackgroundRunRecovery
from omnicoreagent.background.run_helpers import (
    build_run_context,
    is_run_due,
    release_lease_patch,
    result_preview,
    retries_spent,
    retry_delay_seconds,
    run_until_terminal_sleep_seconds,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.core.telemetry import (
    TelemetryContext,
    reset_telemetry_context,
    set_telemetry_context,
)
from omnicoreagent.background.transitions import BackgroundRunTransitions
from omnicoreagent.core.runtime.deadline import (
    complete_despite_cancellation as _complete_despite_cancellation,
    run_with_timeout,
)
from omnicoreagent.governance.capabilities import background_run_authority_request
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.governance.snapshots import (
    attach_policy_snapshot,
    require_current_policy_snapshot,
)


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
        telemetry_store: Any = None,
        governance_engine: Any = None,
        event_log: BackgroundEventLog,
        emit_run: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.task_store = task_store
        self.agents = agents
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.memory_router = memory_router
        self.telemetry_store = telemetry_store
        self.governance_engine = governance_engine
        self.event_log = event_log
        self._emit_run = emit_run
        self.transitions = BackgroundRunTransitions(
            task_store=self.task_store,
            worker_id=lambda: self.worker_id,
            lease_seconds=lambda: self.lease_seconds,
            emit_run=self.emit_run,
        )
        self.recovery = BackgroundRunRecovery(
            task_store=self.task_store,
            transitions=self.transitions,
            worker_id=lambda: self.worker_id,
            lease_seconds=lambda: self.lease_seconds,
            emit_run=self.emit_run,
            checkpoint=self.checkpoint_of,
        )
        self.inline_execution_tasks: dict[str, asyncio.Task] = {}
        self.active_agent_tasks: dict[str, asyncio.Task] = {}
        # Runs this worker stopped because it lost their lease.
        self.fenced_runs: set[str] = set()

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
            if latest.status in SETTLED_RUN_STATUSES:
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

            if deadline is None and latest.status == RunStatus.QUEUED:
                return latest

            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
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

    async def _record_interrupted_attempt(self, running: _RunningAttempt) -> bool:
        """Record a cancelled attempt; return True if cancellation was requested."""
        try:
            if await self.task_store.is_cancel_requested(running.run.run_id):
                await self.mark_attempt_cancelled(running.attempt, running.run)
                await self.mark_terminal(running.run, RunStatus.CANCELLED, "cancelled")
                return True
            await self.handle_attempt_failure(
                running.task,
                running.run,
                running.attempt,
                "exception",
                RuntimeError("worker shutdown"),
            )
            return False
        finally:
            await self.cleanup_running_attempt(running)

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
        if latest.status in {RunStatus.QUEUED, *WAITING_RUN_STATUSES}:
            await self.mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif (
            latest.status == RunStatus.CLAIMED and latest.lease_owner == self.worker_id
        ):
            await self.mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif (
            latest.status == RunStatus.RUNNING and latest.lease_owner == self.worker_id
        ):
            active_task = self.active_agent_tasks.get(run_id)
            if active_task is not None and not active_task.done():
                active_task.cancel()

    async def recover_expired_runs(self) -> None:
        await self.recovery.recover_expired_runs()

    async def checkpoint_of(self, run: BackgroundRun) -> dict[str, Any] | None:
        """The agent's durable record of ``run`` — what ``agent.run(run_id=)``
        would continue from — or None when the agent keeps none."""
        agent = await resolve_agent(
            agent_id=run.agent_id,
            agents=self.agents,
            task_store=self.task_store,
            memory_router=self.memory_router,
            telemetry_store=self.telemetry_store,
        )
        get_run = getattr(agent, "get_run", None)
        if get_run is None:
            return None
        try:
            record = await get_run(run.run_id)
        except Exception as exc:
            logger.warning(
                f"Could not read the checkpoint of run {run.run_id}: "
                f"{exc.__class__.__name__}: {exc}"
            )
            return None
        return record if isinstance(record, dict) else None

    async def recover_expired_run(self, run: BackgroundRun) -> None:
        await self.recovery.recover_expired_run(run)

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

        # Starting an attempt moves the run to RUNNING, creates the attempt,
        # starts its heartbeat, and records lifecycle events. A cancellation
        # in the middle of that used to leave the run RUNNING with a live
        # heartbeat, so the start completes and the interruption is recorded.
        start = asyncio.ensure_future(self.start_claimed_attempt(claimed, task, agent))
        try:
            running = await asyncio.shield(start)
        except asyncio.CancelledError:
            running = await _complete_despite_cancellation(start)
            if running is not None:
                await _complete_despite_cancellation(
                    self._record_interrupted_attempt(running)
                )
            raise
        if running is None:
            return

        try:
            result = await self.execute_agent_attempt(running)
            if (
                isinstance(result, dict)
                # Waiting for a person (an approval, a top-up) is not a
                # failure: the run is parked.
                and result.get("status", "success")
                not in {"success", "awaiting_approval", "awaiting_budget"}
            ):
                raise RuntimeError(
                    f"Agent execution failed ({result.get('termination_reason', result['status'])}): "
                    f"{result.get('response', '')}"
                )
        except asyncio.CancelledError:
            # Shutdown can cancel this task again while the interrupted
            # attempt is being recorded; with stores that do real I/O that
            # left the run RUNNING forever. The bookkeeping finishes first.
            cancelled_by_request = await _complete_despite_cancellation(
                self._record_interrupted_attempt(running)
            )
            if cancelled_by_request:
                return
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
            telemetry_store=self.telemetry_store,
        )
        if agent is None:
            await self.mark_terminal(claimed, RunStatus.FAILED, "agent missing")
            return
        if await self.task_store.is_cancel_requested(claimed.run_id):
            await self.mark_terminal(claimed, RunStatus.CANCELLED, "cancelled")
            return
        try:
            require_current_policy_snapshot(
                claimed.metadata,
                self.governance_engine,
                surface=f"background run {claimed.run_id}",
                required=self.governance_engine is not None,
            )
            require_current_policy_snapshot(
                task.metadata,
                self.governance_engine,
                surface=f"background task {task.task_id}",
                required=self.governance_engine is not None,
            )
            if self.governance_engine is not None and not claimed.metadata.get(
                "governance_run_start_authorized"
            ):
                await self.governance_engine.authorize(
                    background_run_authority_request(
                        action="start",
                        run=claimed,
                        task=task,
                        actor="background_worker",
                    )
                )
                task = task.model_copy(
                    update={
                        "metadata": attach_policy_snapshot(
                            task.metadata,
                            self.governance_engine,
                        ),
                        "updated_at": utc_now(),
                    }
                )
                await self.task_store.save_task(task)
                claimed = await self.task_store.update_run_metadata(
                    claimed.run_id,
                    {
                        **attach_policy_snapshot(
                            claimed.metadata,
                            self.governance_engine,
                        ),
                        "governance_run_start_authorized": True,
                    },
                    self.worker_id,
                    claimed.lease_token,
                )
        except Exception as exc:
            if isinstance(exc, GovernanceError):
                await self.task_store.update_run_metadata(
                    claimed.run_id,
                    {"governance_failure": dict(exc.metadata)},
                    self.worker_id,
                    claimed.lease_token,
                )
            await self.mark_terminal(claimed, RunStatus.FAILED, str(exc))
            return
        return task, agent

    async def attempt_reason(self, run_id: str, attempt_number: int) -> AttemptReason:
        """Why this attempt starts: the first one, a retry after a failure, or
        the continuation of an attempt a lost worker left behind."""
        if attempt_number == 1:
            return AttemptReason.INITIAL
        attempts = await self.task_store.list_attempts(run_id)
        previous = max(attempts, key=lambda item: item.attempt_number, default=None)
        if previous is not None and previous.status == AttemptStatus.INTERRUPTED:
            return AttemptReason.RECOVERY
        return AttemptReason.RETRY

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
            reason=await self.attempt_reason(run.run_id, attempt_number),
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
            self.run_agent_with_run_context(
                agent=running.agent,
                query=query,
                run=running.run,
                timeout_seconds=running.task.timeout_seconds,
                attempt=running.attempt,
            )
        )
        self.track_active_agent_task(running.run.run_id, agent_task)
        try:
            return await agent_task
        except asyncio.CancelledError:
            if running.run.run_id in self.fenced_runs:
                # Stopped by this worker, not by whoever cancelled the worker:
                # the attempt failed for want of its lease, the worker goes on.
                self.fenced_runs.discard(running.run.run_id)
                raise RunLeaseError(f"Lost the lease of run {running.run.run_id}")
            raise

    async def complete_successful_attempt(
        self, running: _RunningAttempt, result: Any
    ) -> None:
        if isinstance(result, dict) and result.get("status") == "awaiting_approval":
            await self.park_for_approval(running, result)
            return
        if isinstance(result, dict) and result.get("status") == "awaiting_budget":
            await self.park_for_budget(running, result)
            return
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

    async def park_for_approval(self, running: _RunningAttempt, result: dict) -> None:
        """The agent paused for approval: the attempt is done, the run waits
        (without a lease) until resume_run queues it again."""
        tools = ", ".join(
            sorted({a.get("tool_name") or "?" for a in result.get("approvals") or []})
        )
        await self.park(
            running,
            status=RunStatus.AWAITING_APPROVAL,
            preview=f"Waiting for approval: {tools}",
            event_name="background_run_awaiting_approval",
        )

    async def park_for_budget(self, running: _RunningAttempt, result: dict) -> None:
        """The agent paused because a budget ran out: the run waits for a
        top-up (agent.grant_budget) and resume_run, or a denial."""
        request = result.get("budget_request") or {}
        needs = (
            f"{request.get('scope')} {request.get('meter')} "
            f"(needs {request.get('shortfall')} more)"
        )
        await self.park(
            running,
            status=RunStatus.AWAITING_BUDGET,
            preview=f"Waiting for budget: {needs}",
            event_name="background_run_awaiting_budget",
        )

    async def park(
        self, running: _RunningAttempt, *, status: RunStatus, preview: str, event_name: str
    ) -> None:
        if await self.cancel_if_requested(running.run, running.attempt):
            return
        await self.task_store.update_attempt(
            running.attempt.attempt_id,
            {"status": AttemptStatus.COMPLETED, "finished_at": utc_now()},
            self.worker_id,
            running.run.lease_token,
        )
        waiting = await self.transition_or_cancel(
            run=running.run,
            attempt=running.attempt,
            expected={RunStatus.RUNNING},
            next_status=status,
            patch={**release_lease_patch(), "result_preview": preview},
        )
        if waiting is not None:
            await self.emit_run(event_name, waiting)

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

    def track_active_agent_task(self, run_id: str, task: asyncio.Task) -> None:
        self.active_agent_tasks[run_id] = task

        def _forget(completed: asyncio.Task) -> None:
            if self.active_agent_tasks.get(run_id) is completed:
                self.active_agent_tasks.pop(run_id, None)

        task.add_done_callback(_forget)

    async def run_agent_with_run_context(
        self,
        *,
        agent: Any,
        query: str,
        run: BackgroundRun,
        timeout_seconds: int | None,
        attempt: BackgroundAttempt | None = None,
    ) -> Any:
        kwargs = {"query": query, "session_id": run.session_id}
        try:
            signature = inspect.signature(agent.run)
            if _accepts_keyword(signature, "run_id"):
                kwargs["run_id"] = run.run_id
        except (TypeError, ValueError):
            kwargs["run_id"] = run.run_id

        context_token = set_telemetry_context(
            TelemetryContext(
                trace_id=self.event_log.telemetry_trace_id(run.run_id),
                span_id=self.event_log.telemetry_span_id(run.run_id),
                run_id=run.run_id,
                session_id=run.session_id,
                task_id=run.task_id,
                agent_id=run.agent_id,
                attempt_id=attempt.attempt_id if attempt is not None else None,
                attempt_number=(
                    attempt.attempt_number if attempt is not None else None
                ),
                execution_surface="background",
            )
        )
        try:
            async def invoke():
                if getattr(agent, "mcp_tools", None):
                    await agent.connect_mcp_servers()
                return await agent.run(**kwargs)

            # A deadline marks the run as timed out, not cancelled, in its trace.
            return await run_with_timeout(invoke(), timeout_seconds)
        finally:
            reset_telemetry_context(context_token)

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
        spent = retries_spent(await self.task_store.list_attempts(run.run_id))
        can_retry = (
            reason in task.retry_policy.retry_on
            and spent <= task.retry_policy.max_retries
        )
        if can_retry:
            retry_delay = retry_delay_seconds(task, spent)
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
            self.fence_lost_run(run_id)
            return
        while True:
            await asyncio.sleep(interval)
            if not await self.refresh_run_lease(run_id, lease_token):
                self.fence_lost_run(run_id)
                return
            try:
                latest = await self.task_store.get_run(run_id)
                if latest and latest.status in {RunStatus.RUNNING, RunStatus.RETRYING}:
                    await self.emit_run("background_run_heartbeat", latest)
            except Exception:
                continue

    async def refresh_run_lease(self, run_id: str, lease_token: str) -> bool:
        return await self.transitions.refresh_run_lease(run_id, lease_token)

    def fence_lost_run(self, run_id: str) -> None:
        """This worker no longer owns the run: stop its agent now. Another
        worker may already have taken the run over from its checkpoint; work
        done past this point would be unrecorded and unfenced."""
        task = self.active_agent_tasks.get(run_id)
        if task is not None and not task.done():
            logger.warning(f"Lost the lease of run {run_id}; stopping its agent")
            self.fenced_runs.add(run_id)
            task.cancel()

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


def _accepts_keyword(signature: inspect.Signature, name: str) -> bool:
    return name in signature.parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
