"""Durable background execution manager."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from omnicoreagent.background.agent_specs import resolve_agent, spec_from_agent
from omnicoreagent.background.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    RunNotFoundError,
    TaskAlreadyRegisteredError,
    TaskNotFoundError,
)
from omnicoreagent.background.models import (
    TERMINAL_RUN_STATUSES,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
    ScheduleSpec,
    TriggerType,
    coerce_model,
    utc_now,
)
from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.run_helpers import (
    build_run,
)
from omnicoreagent.background.scheduler import BackgroundScheduleDispatcher
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.store.router import TaskStoreRouter
from omnicoreagent.background.supervisor import BackgroundSupervisor
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
        self._scheduler = BackgroundScheduleDispatcher(
            task_store=self.task_store,
            event_log=self._event_log,
            emit_run=self._emit_run,
        )
        self._supervisor = BackgroundSupervisor(
            task_store=self.task_store,
            agents=self._agents,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            memory_router=self.memory_router,
            event_router=self.event_router,
            event_log=self._event_log,
            emit_run=self._emit_run,
        )
        self._inline_execution_tasks = self._supervisor.inline_execution_tasks
        self._active_agent_tasks = self._supervisor.active_agent_tasks
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
        self._sync_services_config()
        return await self._supervisor.run_until_terminal(
            run_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    def _get_or_start_inline_execution_task(self, run_id: str) -> asyncio.Task:
        self._sync_services_config()
        return self._supervisor.get_or_start_inline_execution_task(run_id)

    async def _cancel_inline_execution_tasks(self) -> None:
        await self._supervisor.cancel_inline_execution_tasks()

    async def _cancel_active_agent_tasks(self) -> None:
        await self._supervisor.cancel_active_agent_tasks()

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
        self._sync_services_config()
        await self._supervisor.cancel_run(run_id)

    async def recover_expired_runs(self) -> None:
        self._sync_services_config()
        await self._supervisor.recover_expired_runs()

    async def _recover_expired_run(self, run: BackgroundRun) -> None:
        self._sync_services_config()
        await self._supervisor.recover_expired_run(run)

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
        schedule_state = await self.task_store.get_schedule_state(task_id)
        runs = await self.task_store.list_runs(task_id=task_id)
        active = [run for run in runs if run.status not in TERMINAL_RUN_STATUSES]
        status_counts = {status.value: 0 for status in RunStatus}
        for run in runs:
            status_counts[run.status.value] += 1
        latest_run = runs[-1] if runs else None
        return {
            "task_id": task_id,
            "agent_id": task.agent_id,
            "enabled": task.enabled,
            "schedule": task.schedule.model_dump(mode="json"),
            "schedule_state": (
                schedule_state.model_dump(mode="json") if schedule_state else None
            ),
            "runs": len(runs),
            "active_runs": len(active),
            "status_counts": status_counts,
            "latest_run": latest_run.model_dump(mode="json") if latest_run else None,
        }

    async def get_manager_status(self) -> dict[str, Any]:
        agents = await self.task_store.list_agents()
        tasks = await self.task_store.list_tasks()
        runs = await self.task_store.list_runs()
        active = [run for run in runs if run.status not in TERMINAL_RUN_STATUSES]
        status_counts = {status.value: 0 for status in RunStatus}
        for run in runs:
            status_counts[run.status.value] += 1
        return {
            "running": self._running,
            "initialized": self._initialized,
            "worker_id": self.worker_id,
            "lease_seconds": self.lease_seconds,
            "agents": len(agents),
            "tasks": len(tasks),
            "runs": len(runs),
            "active_runs": len(active),
            "status_counts": status_counts,
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
        self._sync_services_config()
        return await self._scheduler.dispatch_due_schedules(limit=limit)

    async def _execute_one(self) -> bool:
        self._sync_services_config()
        return await self._supervisor.execute_one()

    async def _execute_run(self, run_id: str) -> bool:
        self._sync_services_config()
        return await self._supervisor.execute_run(run_id)

    async def _release_claimed_before_start(self, run_id: str) -> None:
        self._sync_services_config()
        await self._supervisor.release_claimed_before_start(run_id)

    async def _run_claimed(self, claimed: BackgroundRun) -> None:
        self._sync_services_config()
        await self._supervisor.run_claimed(claimed)

    async def _run_agent_with_event_context(
        self,
        *,
        agent: Any,
        query: str,
        run: BackgroundRun,
        timeout_seconds: int | None,
    ) -> Any:
        return await self._supervisor.run_agent_with_event_context(
            agent=agent,
            query=query,
            run=run,
            timeout_seconds=timeout_seconds,
        )

    async def _handle_attempt_failure(
        self,
        task: BackgroundTaskSpec,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        reason: str,
        exc: BaseException,
    ) -> None:
        self._sync_services_config()
        await self._supervisor.handle_attempt_failure(task, run, attempt, reason, exc)

    async def _mark_terminal(
        self, run: BackgroundRun, status: RunStatus, error: str | None
    ) -> None:
        self._sync_services_config()
        await self._supervisor.mark_terminal(run, status, error)

    async def _heartbeat_until_finished(
        self, run_id: str, lease_token: str | None
    ) -> None:
        self._sync_services_config()
        await self._supervisor.heartbeat_until_finished(run_id, lease_token)

    async def _refresh_run_lease(self, run_id: str, lease_token: str) -> bool:
        self._sync_services_config()
        return await self._supervisor.refresh_run_lease(run_id, lease_token)

    async def _drain_cancelled_task(self, task: asyncio.Task) -> None:
        await self._supervisor.drain_cancelled_task(task)

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

    def _sync_services_config(self) -> None:
        self._sync_event_log_config()
        self._supervisor.worker_id = self.worker_id
        self._supervisor.memory_router = self.memory_router
        self._supervisor.event_router = self.event_router
        self._supervisor.lease_seconds = self.lease_seconds

    def _sync_event_log_config(self) -> None:
        self._event_log.event_router = self.event_router
        self._event_log.replay_timeout_seconds = self._event_replay_timeout_seconds
        self._event_log.append_timeout_seconds = self._event_append_timeout_seconds
