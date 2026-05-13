"""Durable background execution manager."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

from omnicoreagent.core.events.base import reset_event_run_id, set_event_run_id
from omnicoreagent.background.agent_specs import resolve_agent, spec_from_agent
from omnicoreagent.background.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    RunLeaseError,
    RunNotFoundError,
    TaskAlreadyRegisteredError,
    TaskNotFoundError,
)
from omnicoreagent.background.models import (
    TERMINAL_RUN_STATUSES,
    AttemptReason,
    AttemptStatus,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
    ScheduleSpec,
    TriggerType,
    coerce_model,
    next_schedule_due,
    utc_now,
)
from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.run_helpers import (
    build_run,
    build_run_context,
    is_run_due,
    release_lease_patch,
    result_preview,
    retry_delay_seconds,
    run_until_terminal_sleep_seconds,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.store.router import TaskStoreRouter
from omnicoreagent.background.workspace_io import BackgroundWorkspaceIO


_EVENT_REPLAY_TIMEOUT_SECONDS = 2.0
_EVENT_APPEND_TIMEOUT_SECONDS = 2.0


class BackgroundAgentManager:
    """Facade for durable background execution around OmniCoreAgent."""

    def __init__(
        self,
        task_store: str | dict[str, Any] | AbstractTaskStore | None = None,
        memory_router: Any = None,
        event_router: Any = None,
        workspace: Any = None,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ) -> None:
        self.task_store = TaskStoreRouter.create(task_store)
        self.memory_router = memory_router
        self.event_router = event_router
        self.worker_id = worker_id or f"worker_{uuid4().hex}"
        self.lease_seconds = lease_seconds

        self._agents: dict[str, Any] = {}
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._inline_execution_tasks: dict[str, asyncio.Task] = {}
        self._active_agent_tasks: dict[str, asyncio.Task] = {}
        self._event_replay_timeout_seconds = _EVENT_REPLAY_TIMEOUT_SECONDS
        self._event_append_timeout_seconds = _EVENT_APPEND_TIMEOUT_SECONDS
        self._workspace_io = BackgroundWorkspaceIO(workspace)
        self._event_log = BackgroundEventLog(
            task_store=self.task_store,
            workspace_io=self._workspace_io,
            event_router=self.event_router,
            replay_timeout_seconds=self._event_replay_timeout_seconds,
            append_timeout_seconds=self._event_append_timeout_seconds,
        )
        self._events = self._event_log.local_events
        self._event_sequences = self._event_log.event_sequences
        self._event_router_tasks = self._event_log.router_tasks
        self._initialized = False

    async def register_agent(
        self, agent_id: str, agent: Any, replace: bool = False
    ) -> BackgroundAgentSpec:
        existing = await self.task_store.get_agent(agent_id)
        if existing and not replace:
            raise AgentAlreadyRegisteredError(f"Agent already registered: {agent_id}")

        spec = spec_from_agent(agent_id, agent)
        self._agents[agent_id] = agent
        await self.task_store.save_agent(spec)
        return spec

    async def register_agent_spec(
        self, spec: BackgroundAgentSpec | dict[str, Any], replace: bool = False
    ) -> BackgroundAgentSpec:
        agent_spec = coerce_model(BackgroundAgentSpec, spec)
        existing = await self.task_store.get_agent(agent_spec.agent_id)
        if existing and not replace:
            raise AgentAlreadyRegisteredError(
                f"Agent already registered: {agent_spec.agent_id}"
        )
        self._agents.pop(agent_spec.agent_id, None)
        await self.task_store.save_agent(agent_spec)
        return agent_spec

    async def unregister_agent(self, agent_id: str, force: bool = False) -> None:
        active = await self.task_store.list_active_runs()
        if not force and any(run.agent_id == agent_id for run in active):
            raise AgentAlreadyRegisteredError(f"Agent has active runs: {agent_id}")
        self._agents.pop(agent_id, None)
        await self.task_store.delete_agent(agent_id)

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        return await self.task_store.get_agent(agent_id)

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        return await self.task_store.list_agents()

    async def register_task(
        self,
        spec: BackgroundTaskSpec | dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        query: str | None = None,
        schedule: ScheduleSpec | dict[str, Any] | None = None,
        enabled: bool = True,
        timeout_seconds: int | None = None,
        retry_policy: dict[str, Any] | None = None,
        overlap_policy: OverlapPolicy | str | None = None,
        session_policy: dict[str, Any] | None = None,
        workspace_policy: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> BackgroundTaskSpec:
        if spec is not None and any([task_id, agent_id, query, schedule]):
            raise ValueError("Pass either spec or task keyword fields, not both")

        task = (
            coerce_model(BackgroundTaskSpec, spec)
            if spec is not None
            else BackgroundTaskSpec(
                task_id=task_id,
                agent_id=agent_id,
                query=query,
                schedule=schedule,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                retry_policy=retry_policy or {},
                overlap_policy=overlap_policy or OverlapPolicy.SKIP_IF_RUNNING,
                session_policy=session_policy or {},
                workspace_policy=workspace_policy or {},
                metadata=metadata or {},
            )
        )
        if not await self.task_store.get_agent(task.agent_id):
            raise AgentNotFoundError(f"Agent not found: {task.agent_id}")
        if await self.task_store.get_task(task.task_id) and not replace:
            raise TaskAlreadyRegisteredError(f"Task already registered: {task.task_id}")
        await self.task_store.save_task(task)
        return task

    async def update_task(self, task_id: str, patch: dict[str, Any]) -> BackgroundTaskSpec:
        existing = await self.task_store.get_task(task_id)
        if not existing:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        data = existing.model_dump(mode="python")
        data.update(patch)
        data["updated_at"] = utc_now()
        updated = BackgroundTaskSpec(**data)
        await self.task_store.save_task(updated)
        return updated

    async def delete_task(self, task_id: str, delete_runs: bool = False) -> None:
        await self.task_store.delete_task(task_id)
        if delete_runs:
            await self.task_store.delete_runs_for_task(task_id)

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        return await self.task_store.get_task(task_id)

    async def list_tasks(self, agent_id: str | None = None) -> list[BackgroundTaskSpec]:
        return await self.task_store.list_tasks(agent_id=agent_id)

    async def start(self) -> None:
        if self._running:
            return
        await self.initialize()
        self._stop_event.clear()
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.task_store.initialize()
        self._initialized = True

    async def shutdown(self) -> None:
        if self._running:
            self._running = False
            self._stop_event.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        await self._cancel_active_agent_tasks()
        await self._cancel_inline_execution_tasks()
        await self._event_log.cancel_router_tasks()
        await self.task_store.close()
        self._initialized = False

    async def pause_task(self, task_id: str) -> None:
        await self.task_store.set_schedule_paused(task_id, True)

    async def resume_task(self, task_id: str) -> None:
        await self.task_store.set_schedule_paused(task_id, False)

    async def run_now(
        self,
        task_id: str,
        query: str | None = None,
        wait: bool = False,
        timeout_seconds: float | None = None,
    ) -> BackgroundRun:
        task = await self.task_store.get_task(task_id)
        if not task or not task.enabled:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        run = build_run(task, TriggerType.MANUAL, query or task.query)
        created = await self.task_store.create_run_with_overlap_guard(
            run, task.overlap_policy
        )
        await self._emit_run(
            "background_run_skipped"
            if created.status == RunStatus.SKIPPED
            else "background_run_queued",
            created,
        )
        if wait and created.status == RunStatus.QUEUED:
            if self._running:
                return await self.wait_for_run(
                    created.run_id,
                    timeout_seconds=timeout_seconds,
                )
            return await self.run_until_terminal(
                created.run_id,
                timeout_seconds=timeout_seconds,
            )
        return created

    async def run_until_terminal(
        self,
        run_id: str,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ) -> BackgroundRun:
        """Drive a queued run until terminal state or timeout using manager semantics."""
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
                execution_task = self._get_or_start_inline_execution_task(run_id)
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
                        if deadline is None:
                            return await self.task_store.get_run(run_id) or latest
                        latest = await self.task_store.get_run(run_id) or latest
                    else:
                        continue
                except asyncio.TimeoutError:
                    return await self.task_store.get_run(run_id) or latest

            if deadline is None:
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

    def _get_or_start_inline_execution_task(self, run_id: str) -> asyncio.Task:
        existing = self._inline_execution_tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self._execute_run(run_id))
        self._inline_execution_tasks[run_id] = task

        def _forget(completed: asyncio.Task) -> None:
            if self._inline_execution_tasks.get(run_id) is completed:
                self._inline_execution_tasks.pop(run_id, None)

        task.add_done_callback(_forget)
        return task

    async def _cancel_inline_execution_tasks(self) -> None:
        pending = [task for task in self._inline_execution_tasks.values() if not task.done()]
        if not pending:
            self._inline_execution_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._inline_execution_tasks.clear()

    async def _cancel_active_agent_tasks(self) -> None:
        pending = [task for task in self._active_agent_tasks.values() if not task.done()]
        if not pending:
            self._active_agent_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._active_agent_tasks.clear()

    async def wait_for_run(
        self,
        run_id: str,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ) -> BackgroundRun:
        """Wait for one specific run to become terminal without executing work."""
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
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                return latest
            sleep_seconds = poll_interval_seconds
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return latest
                sleep_seconds = min(sleep_seconds, remaining)
            await asyncio.sleep(max(sleep_seconds, 0.001))

    async def cancel_run(self, run_id: str) -> None:
        run = await self.task_store.get_run(run_id)
        if not run:
            raise RunNotFoundError(f"Run not found: {run_id}")
        await self.task_store.request_cancel(run_id)
        latest = await self.task_store.get_run(run_id)
        if not latest:
            return
        if latest.status == RunStatus.QUEUED:
            await self._mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif (
            latest.status == RunStatus.CLAIMED
            and latest.lease_owner == self.worker_id
        ):
            await self._mark_terminal(latest, RunStatus.CANCELLED, "cancelled")
        elif latest.status == RunStatus.RUNNING and latest.lease_owner == self.worker_id:
            active_task = self._active_agent_tasks.get(run_id)
            if active_task is not None and not active_task.done():
                active_task.cancel()

    async def recover_expired_runs(self) -> None:
        expired = await self.task_store.list_expired_leases(utc_now())
        for run in expired:
            try:
                await self._recover_expired_run(run)
            except RunLeaseError:
                continue

    async def _recover_expired_run(self, run: BackgroundRun) -> None:
        stolen = await self.task_store.steal_expired_run(
            run.run_id, self.worker_id, self.lease_seconds
        )
        if await self.task_store.is_cancel_requested(stolen.run_id):
            await self._mark_terminal(stolen, RunStatus.CANCELLED, "cancelled")
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
            await self._mark_terminal(stolen, RunStatus.FAILED, "task missing")
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
            await self._emit_run("background_run_recovered", recovered)
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
            await self._emit_run("background_run_recovered", recovered)
            return

        await self._mark_terminal(stolen, RunStatus.FAILED, "lease expired")

    async def get_run(self, run_id: str) -> BackgroundRun | None:
        return await self.task_store.get_run(run_id)

    async def list_runs(
        self, task_id: str | None = None, status: str | RunStatus | None = None
    ) -> list[BackgroundRun]:
        run_status = RunStatus(status) if isinstance(status, str) else status
        return await self.task_store.list_runs(task_id=task_id, status=run_status)

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        return await self.task_store.list_attempts(run_id)

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        task = await self.task_store.get_task(task_id)
        if not task:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        runs = await self.task_store.list_runs(task_id=task_id)
        active = [run for run in runs if run.status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMEOUT,
            RunStatus.SKIPPED,
        }]
        return {"task_id": task_id, "enabled": task.enabled, "runs": len(runs), "active": len(active)}

    async def get_manager_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "agents": len(await self.task_store.list_agents()),
            "tasks": len(await self.task_store.list_tasks()),
        }

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        run = await self.task_store.get_run(run_id)
        if not run:
            return []
        self._sync_event_log_config()
        return await self._event_log.get_run_events(run)

    async def get_run_workspace(self, run_id: str) -> dict[str, Any]:
        """Return the durable workspace location and visible files for a run."""
        run = await self.task_store.get_run(run_id)
        if not run:
            raise RunNotFoundError(f"Run not found: {run_id}")

        return self._workspace_io.run_files(run)

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.recover_expired_runs()
                dispatched = await self._dispatch_due_schedules()
                did_work = await self._execute_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                dispatched = False
                did_work = False
            if not dispatched and not did_work:
                await asyncio.sleep(0.05)

    async def _dispatch_due_schedules(self, limit: int = 25) -> bool:
        dispatched_any = False
        due_items = await self.task_store.get_due_schedules(utc_now(), limit=limit)
        for task, state, occurrence_id in due_items:
            if state.next_due_at is None:
                continue
            trigger = TriggerType(task.schedule.type.value)
            run = build_run(
                task,
                trigger,
                task.query,
                due_at=state.next_due_at,
                occurrence_id=occurrence_id,
            )
            existing_run_ids = {
                item.run_id for item in await self.task_store.list_runs(task.task_id)
            }
            next_due_at = next_schedule_due(task.schedule, state.next_due_at, utc_now())
            created = await self.task_store.dispatch_scheduled_run(
                run,
                task.overlap_policy,
                state.schedule_revision,
                next_due_at,
            )
            dispatched_any = True
            if created.run_id in existing_run_ids:
                continue
            await self._emit_run("background_task_scheduled", created)
            await self._emit_run(
                "background_run_skipped"
                if created.status == RunStatus.SKIPPED
                else "background_run_queued",
                created,
            )
        return dispatched_any

    async def _execute_one(self) -> bool:
        claimed = await self.task_store.claim_next_run(
            self.worker_id, self.lease_seconds
        )
        if not claimed:
            return False
        try:
            await self._run_claimed(claimed)
        except asyncio.CancelledError:
            await self._release_claimed_before_start(claimed.run_id)
            raise
        return True

    async def _execute_run(self, run_id: str) -> bool:
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
            await self._run_claimed(claimed)
        except asyncio.CancelledError:
            await self._release_claimed_before_start(claimed.run_id)
            raise
        return True

    async def _release_claimed_before_start(self, run_id: str) -> None:
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
        await self._emit_run("background_run_queued", released)

    async def _run_claimed(self, claimed: BackgroundRun) -> None:
        task = await self.task_store.get_task(claimed.task_id)
        if not task:
            await self._mark_terminal(claimed, RunStatus.FAILED, "task missing")
            return
        agent = await resolve_agent(
            agent_id=claimed.agent_id,
            agents=self._agents,
            task_store=self.task_store,
            memory_router=self.memory_router,
            event_router=self.event_router,
        )
        if agent is None:
            await self._mark_terminal(claimed, RunStatus.FAILED, "agent missing")
            return
        if await self.task_store.is_cancel_requested(claimed.run_id):
            await self._mark_terminal(claimed, RunStatus.CANCELLED, "cancelled")
            return

        if claimed.lease_token is not None:
            await self._refresh_run_lease(claimed.run_id, claimed.lease_token)
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
            if not await self._refresh_run_lease(run.run_id, run.lease_token):
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
            await self._mark_terminal(run, RunStatus.CANCELLED, "cancelled")
            return
        heartbeat_task = asyncio.create_task(
            self._heartbeat_until_finished(run.run_id, run.lease_token)
        )
        await self._emit_run("background_run_claimed", claimed)
        await self._emit_run("background_run_started", run)

        try:
            if await self.task_store.is_cancel_requested(run.run_id):
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
                await self._mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            query = build_run_context(run)
            agent_task = asyncio.create_task(
                self._run_agent_with_event_context(
                    agent=agent,
                    query=query,
                    run=run,
                    timeout_seconds=task.timeout_seconds,
                )
            )
            self._active_agent_tasks[run.run_id] = agent_task
            result = await agent_task
        except asyncio.CancelledError:
            try:
                if await self.task_store.is_cancel_requested(run.run_id):
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
                    await self._mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                    return
                await self._handle_attempt_failure(
                    task, run, attempt, "exception", RuntimeError("worker shutdown")
                )
            finally:
                self._active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            raise
        except asyncio.TimeoutError as exc:
            try:
                await self._handle_attempt_failure(task, run, attempt, "timeout", exc)
            finally:
                self._active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            return
        except Exception as exc:
            try:
                await self._handle_attempt_failure(task, run, attempt, "exception", exc)
            finally:
                self._active_agent_tasks.pop(run.run_id, None)
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            return

        try:
            preview = result_preview(result)
            if await self.task_store.is_cancel_requested(run.run_id):
                if run.lease_token is not None:
                    if not await self._refresh_run_lease(run.run_id, run.lease_token):
                        return
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
                await self._mark_terminal(run, RunStatus.CANCELLED, "cancelled")
                return
            if run.lease_token is not None:
                if not await self._refresh_run_lease(run.run_id, run.lease_token):
                    return
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"status": AttemptStatus.COMPLETED, "finished_at": utc_now()},
                self.worker_id,
                run.lease_token,
            )
            if run.lease_token is not None:
                if not await self._refresh_run_lease(run.run_id, run.lease_token):
                    return
            completed = await self.task_store.transition_run(
                run.run_id,
                {RunStatus.RUNNING},
                RunStatus.COMPLETED,
                {"result_preview": preview},
                self.worker_id,
                run.lease_token,
            )
            await self._emit_run("background_run_completed", completed)
        finally:
            self._active_agent_tasks.pop(run.run_id, None)
            heartbeat_task.cancel()
            await self._drain_cancelled_task(heartbeat_task)

    async def _run_agent_with_event_context(
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

    async def _handle_attempt_failure(
        self,
        task: BackgroundTaskSpec,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        reason: str,
        exc: BaseException,
    ) -> None:
        status = AttemptStatus.TIMEOUT if reason == "timeout" else AttemptStatus.FAILED
        if run.lease_token is not None:
            if not await self._refresh_run_lease(run.run_id, run.lease_token):
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
                if not await self._refresh_run_lease(run.run_id, run.lease_token):
                    return
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"retry_delay_seconds": retry_delay},
                self.worker_id,
                run.lease_token,
            )
            if run.lease_token is not None:
                if not await self._refresh_run_lease(run.run_id, run.lease_token):
                    return
            retrying = await self.task_store.transition_run(
                run.run_id,
                {RunStatus.RUNNING},
                RunStatus.RETRYING,
                {"error": str(exc)},
                self.worker_id,
                run.lease_token,
            )
            await self._emit_run("background_run_retrying", retrying)
            await self.task_store.transition_run(
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
            return
        terminal = RunStatus.TIMEOUT if reason == "timeout" else RunStatus.FAILED
        await self._mark_terminal(run, terminal, str(exc))

    async def _mark_terminal(
        self, run: BackgroundRun, status: RunStatus, error: str | None
    ) -> None:
        latest = await self.task_store.get_run(run.run_id)
        if not latest:
            return
        if latest.lease_token is not None:
            if not await self._refresh_run_lease(latest.run_id, latest.lease_token):
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
        await self._emit_run(f"background_run_{status.value}", terminal)

    async def _heartbeat_until_finished(
        self, run_id: str, lease_token: str | None
    ) -> None:
        if lease_token is None:
            return
        interval = max(0.01, self.lease_seconds / 4)
        if not await self._refresh_run_lease(run_id, lease_token):
            return
        while True:
            await asyncio.sleep(interval)
            if not await self._refresh_run_lease(run_id, lease_token):
                return
            try:
                latest = await self.task_store.get_run(run_id)
                if latest and latest.status in {RunStatus.RUNNING, RunStatus.RETRYING}:
                    await self._emit_run("background_run_heartbeat", latest)
            except Exception:
                continue

    async def _refresh_run_lease(self, run_id: str, lease_token: str) -> bool:
        try:
            await self.task_store.refresh_lease(
                run_id, self.worker_id, lease_token, self.lease_seconds
            )
            return True
        except Exception:
            return False

    async def _drain_cancelled_task(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _resolve_agent(self, agent_id: str) -> Any | None:
        return await resolve_agent(
            agent_id=agent_id,
            agents=self._agents,
            task_store=self.task_store,
            memory_router=self.memory_router,
            event_router=self.event_router,
        )

    async def _emit_run(
        self, event_name: str, run: BackgroundRun, **extra_payload: Any
    ) -> None:
        self._sync_event_log_config()
        await self._event_log.emit_run(event_name, run, **extra_payload)

    async def _emit(self, event_name: str, **payload: Any) -> None:
        self._sync_event_log_config()
        await self._event_log.emit(event_name, **payload)

    async def _drain_event_router_tasks(self, run_id: str) -> None:
        self._sync_event_log_config()
        await self._event_log.drain_router_tasks(run_id)

    async def _cancel_event_router_tasks(self) -> None:
        self._sync_event_log_config()
        await self._event_log.cancel_router_tasks()

    async def _write_run_snapshot(self, run: BackgroundRun) -> None:
        await self._event_log.write_run_snapshot(run)

    async def _write_run_event(self, event: dict[str, Any]) -> None:
        await self._event_log.write_run_event(event)

    async def _append_event_router(self, event: dict[str, Any]) -> None:
        self._sync_event_log_config()
        await self._event_log.append_event_router(event)

    async def _read_event_router_events(self, run: BackgroundRun) -> list[dict[str, Any]]:
        self._sync_event_log_config()
        return await self._event_log.read_event_router_events(run)

    @staticmethod
    def _prepare_event_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return BackgroundEventLog.prepare_event_trace(events)

    def _resolve_workspace(self) -> Any | None:
        return self._workspace_io.resolve()

    def _read_workspace_events(self, workspace_path: str) -> list[dict[str, Any]]:
        return self._workspace_io.read_events(workspace_path)

    def _sync_event_log_config(self) -> None:
        self._event_log.event_router = self.event_router
        self._event_log.replay_timeout_seconds = self._event_replay_timeout_seconds
        self._event_log.append_timeout_seconds = self._event_append_timeout_seconds
