"""Durable background execution manager."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

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
    INITIAL_EVENT_NAMES,
    TERMINAL_EVENT_NAMES,
    AttemptReason,
    AttemptStatus,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    BackoffPolicy,
    OverlapPolicy,
    RunStatus,
    ScheduleSpec,
    TriggerType,
    build_session_id,
    build_workspace_path,
    coerce_model,
    next_schedule_due,
    utc_now,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.store.router import TaskStoreRouter


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
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._event_sequences: dict[str, int] = {}
        self._event_replay_timeout_seconds = _EVENT_REPLAY_TIMEOUT_SECONDS
        self._event_append_timeout_seconds = _EVENT_APPEND_TIMEOUT_SECONDS
        self._workspace = workspace
        self._initialized = False

    async def register_agent(
        self, agent_id: str, agent: Any, replace: bool = False
    ) -> BackgroundAgentSpec:
        existing = await self.task_store.get_agent(agent_id)
        if existing and not replace:
            raise AgentAlreadyRegisteredError(f"Agent already registered: {agent_id}")

        spec = BackgroundAgentSpec(
            agent_id=agent_id,
            name=getattr(agent, "name", agent_id),
            system_instruction=getattr(agent, "system_instruction", None),
            model_config=self._safe_model_config(agent),
            agent_config=self._safe_dict(getattr(agent, "agent_config", {})),
            mcp_tools=self._safe_list(getattr(agent, "mcp_tools", [])),
            workspace_config=self._safe_workspace_config(agent),
        )
        self._agents[agent_id] = agent
        await self.task_store.save_agent(spec)
        await self._emit("background_agent_registered", agent_id=agent_id)
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
        await self._emit("background_agent_registered", agent_id=agent_spec.agent_id)
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
        await self._emit(
            "background_task_registered",
            agent_id=task.agent_id,
            task_id=task.task_id,
        )
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
        await self._emit("background_task_deleted", task_id=task_id)

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
        await self.task_store.close()
        self._initialized = False

    async def pause_task(self, task_id: str) -> None:
        await self.task_store.set_schedule_paused(task_id, True)
        await self._emit("background_task_paused", task_id=task_id)

    async def resume_task(self, task_id: str) -> None:
        await self.task_store.set_schedule_paused(task_id, False)
        await self._emit("background_task_resumed", task_id=task_id)

    async def run_now(
        self, task_id: str, query: str | None = None, wait: bool = False
    ) -> BackgroundRun:
        task = await self.task_store.get_task(task_id)
        if not task or not task.enabled:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        run = self._build_run(task, TriggerType.MANUAL, query or task.query)
        created = await self.task_store.create_run_with_overlap_guard(
            run, task.overlap_policy
        )
        await self._emit_run("background_run_queued", created)
        if wait and created.status == RunStatus.QUEUED:
            while True:
                latest = await self.task_store.get_run(created.run_id)
                if latest and latest.status in TERMINAL_RUN_STATUSES:
                    return latest
                did_work = (
                    await self._execute_run(created.run_id)
                    if latest and latest.status == RunStatus.QUEUED
                    else False
                )
                if not did_work:
                    return latest or created
        return created

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
            await asyncio.sleep(poll_interval_seconds)

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

    async def recover_expired_runs(self) -> None:
        expired = await self.task_store.list_expired_leases(utc_now())
        for run in expired:
            stolen = await self.task_store.steal_expired_run(
                run.run_id, self.worker_id, self.lease_seconds
            )
            if await self.task_store.is_cancel_requested(stolen.run_id):
                await self._mark_terminal(stolen, RunStatus.CANCELLED, "cancelled")
                continue

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
                continue

            if stolen.status == RunStatus.CLAIMED:
                recovered = await self.task_store.transition_run(
                    stolen.run_id,
                    {RunStatus.CLAIMED},
                    RunStatus.QUEUED,
                    self._release_lease_patch(),
                    self.worker_id,
                    stolen.lease_token,
                )
                await self._emit_run("background_run_recovered", recovered)
                continue

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
                    self._release_lease_patch(),
                    self.worker_id,
                    retrying.lease_token,
                )
                await self._emit_run("background_run_recovered", recovered)
                continue

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
        events = self._prepare_event_trace(self._events.get(run_id) or [])
        workspace_events = self._prepare_event_trace(
            self._read_workspace_events(run.workspace_path)
        )
        router_events = self._prepare_event_trace(
            await self._read_event_router_events(run)
        )
        candidates = [router_events, events, workspace_events]
        complete = [
            candidate
            for candidate in candidates
            if candidate and candidate[-1].get("event") in TERMINAL_EVENT_NAMES
        ]
        if complete:
            return max(complete, key=len)
        if router_events:
            return router_events
        if events:
            return events
        return workspace_events

    async def get_run_workspace(self, run_id: str) -> dict[str, Any]:
        """Return the durable workspace location and visible files for a run."""
        run = await self.task_store.get_run(run_id)
        if not run:
            raise RunNotFoundError(f"Run not found: {run_id}")

        workspace = self._resolve_workspace()
        files = []
        if workspace is not None:
            for item in workspace.files.list_files(run.workspace_path):
                files.append(
                    {
                        "path": item.path,
                        "name": item.name,
                        "modified_at": item.modified_at.isoformat(),
                        "is_dir": item.is_dir,
                    }
                )

        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "workspace_path": run.workspace_path,
            "files": files,
        }

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.recover_expired_runs()
            dispatched = await self._dispatch_due_schedules()
            did_work = await self._execute_one()
            if not dispatched and not did_work:
                await asyncio.sleep(0.05)

    async def _dispatch_due_schedules(self, limit: int = 25) -> bool:
        dispatched_any = False
        due_items = await self.task_store.get_due_schedules(utc_now(), limit=limit)
        for task, state, occurrence_id in due_items:
            if state.next_due_at is None:
                continue
            trigger = TriggerType(task.schedule.type.value)
            run = self._build_run(
                task,
                trigger,
                task.query,
                due_at=state.next_due_at,
                occurrence_id=occurrence_id,
            )
            next_due_at = next_schedule_due(task.schedule, state.next_due_at, utc_now())
            created = await self.task_store.dispatch_scheduled_run(
                run,
                task.overlap_policy,
                state.schedule_revision,
                next_due_at,
            )
            dispatched_any = True
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
        await self.task_store.transition_run(
            latest.run_id,
            {RunStatus.CLAIMED},
            RunStatus.QUEUED,
            self._release_lease_patch(),
            self.worker_id,
            latest.lease_token,
        )

    async def _run_claimed(self, claimed: BackgroundRun) -> None:
        task = await self.task_store.get_task(claimed.task_id)
        if not task:
            await self._mark_terminal(claimed, RunStatus.FAILED, "task missing")
            return
        agent = await self._resolve_agent(claimed.agent_id)
        if agent is None:
            await self._mark_terminal(claimed, RunStatus.FAILED, "agent missing")
            return
        if await self.task_store.is_cancel_requested(claimed.run_id):
            await self._mark_terminal(claimed, RunStatus.CANCELLED, "cancelled")
            return

        attempt_number = claimed.attempt + 1
        run = await self.task_store.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            {"started_at": utc_now(), "attempt": attempt_number},
            self.worker_id,
            claimed.lease_token,
        )
        attempt = BackgroundAttempt(
            run_id=run.run_id,
            attempt_number=attempt_number,
            reason=AttemptReason.INITIAL if attempt_number == 1 else AttemptReason.RETRY,
            worker_id=self.worker_id,
            lease_token=run.lease_token,
        )
        await self.task_store.create_attempt(attempt)
        await self._emit_run("background_run_started", run)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_until_finished(run.run_id, run.lease_token)
        )

        try:
            query = self._build_run_context(run)
            coro = agent.run(query=query, session_id=run.session_id)
            result = (
                await asyncio.wait_for(coro, timeout=task.timeout_seconds)
                if task.timeout_seconds
                else await coro
            )
        except asyncio.CancelledError:
            try:
                await self._handle_attempt_failure(
                    task, run, attempt, "exception", RuntimeError("worker shutdown")
                )
            finally:
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            raise
        except asyncio.TimeoutError as exc:
            try:
                await self._handle_attempt_failure(task, run, attempt, "timeout", exc)
            finally:
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            return
        except Exception as exc:
            try:
                await self._handle_attempt_failure(task, run, attempt, "exception", exc)
            finally:
                heartbeat_task.cancel()
                await self._drain_cancelled_task(heartbeat_task)
            return

        try:
            preview = self._result_preview(result)
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
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"status": AttemptStatus.COMPLETED, "finished_at": utc_now()},
                self.worker_id,
                run.lease_token,
            )
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
            heartbeat_task.cancel()
            await self._drain_cancelled_task(heartbeat_task)

    async def _handle_attempt_failure(
        self,
        task: BackgroundTaskSpec,
        run: BackgroundRun,
        attempt: BackgroundAttempt,
        reason: str,
        exc: BaseException,
    ) -> None:
        status = AttemptStatus.TIMEOUT if reason == "timeout" else AttemptStatus.FAILED
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
            retry_delay_seconds = self._retry_delay_seconds(task, run.attempt)
            await self.task_store.update_attempt(
                attempt.attempt_id,
                {"retry_delay_seconds": retry_delay_seconds},
                self.worker_id,
                run.lease_token,
            )
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
                    **self._release_lease_patch(),
                    "queued_at": utc_now() + timedelta(seconds=retry_delay_seconds),
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
        while True:
            await asyncio.sleep(interval)
            try:
                await self.task_store.refresh_lease(
                    run_id, self.worker_id, lease_token, self.lease_seconds
                )
            except Exception:
                return

    async def _drain_cancelled_task(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _retry_delay_seconds(self, task: BackgroundTaskSpec, attempt_number: int) -> int:
        policy = task.retry_policy
        if policy.max_retries <= 0:
            return 0
        if policy.backoff == BackoffPolicy.EXPONENTIAL:
            delay = policy.initial_delay_seconds * (2 ** max(attempt_number - 1, 0))
        else:
            delay = policy.initial_delay_seconds
        return min(delay, policy.max_delay_seconds)

    @staticmethod
    def _release_lease_patch() -> dict[str, Any]:
        return {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
        }

    def _build_run(
        self,
        task: BackgroundTaskSpec,
        trigger: TriggerType,
        query: str,
        due_at: datetime | None = None,
        occurrence_id: str | None = None,
    ) -> BackgroundRun:
        run_id = f"run_{uuid4().hex}"
        return BackgroundRun(
            run_id=run_id,
            task_id=task.task_id,
            agent_id=task.agent_id,
            max_attempts=task.retry_policy.max_retries + 1,
            query_snapshot=query,
            trigger_type=trigger,
            due_at=due_at,
            occurrence_id=occurrence_id,
            session_id=build_session_id(task, run_id),
            workspace_path=build_workspace_path(task, run_id),
        )

    def _build_run_context(self, run: BackgroundRun) -> str:
        return (
            "This is a background run.\n"
            f"run_id: {run.run_id}\n"
            f"task_id: {run.task_id}\n"
            f"workspace_path: /workspace/{run.workspace_path}\n"
            "Use output.md for the final durable result.\n\n"
            f"{run.query_snapshot}"
        )

    async def _resolve_agent(self, agent_id: str) -> Any | None:
        agent = self._agents.get(agent_id)
        if agent is not None:
            return agent
        spec = await self.task_store.get_agent(agent_id)
        if not spec or not spec.llm_model_config:
            return None

        from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

        agent = OmniCoreAgent(
            name=spec.name or spec.agent_id,
            system_instruction=spec.system_instruction or "",
            model_config=spec.llm_model_config,
            mcp_tools=spec.mcp_tools,
            agent_config={
                **spec.agent_config,
                **(
                    {"workspace_config": spec.workspace_config}
                    if spec.workspace_config
                    else {}
                ),
            },
            memory_router=self.memory_router,
            event_router=self.event_router,
        )
        self._agents[agent_id] = agent
        return agent

    async def _emit_run(self, event_name: str, run: BackgroundRun) -> None:
        await self._emit(
            event_name,
            agent_id=run.agent_id,
            task_id=run.task_id,
            run_id=run.run_id,
            session_id=run.session_id,
            status=run.status.value,
            attempt=run.attempt,
            workspace_path=run.workspace_path,
        )
        await self._write_run_snapshot(run)

    async def _emit(self, event_name: str, **payload: Any) -> None:
        run_id = payload.get("run_id")
        event = {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if run_id:
            events = self._events.setdefault(run_id, [])
            event["sequence"] = await self._next_run_event_sequence(
                run_id, event_name, events
            )
            events.append(event)
            await self._write_run_event(event)
            await self._append_event_router(event)

    async def _next_run_event_sequence(
        self, run_id: str, event_name: str, local_events: list[dict[str, Any]]
    ) -> int:
        cached = self._event_sequences.get(run_id)
        if cached is not None:
            self._event_sequences[run_id] = cached + 1
            return cached + 1
        sequences = [
            int(event["sequence"])
            for event in local_events
            if isinstance(event.get("sequence"), int)
        ]
        if sequences:
            next_sequence = max(sequences) + 1
            self._event_sequences[run_id] = next_sequence
            return next_sequence
        if event_name in INITIAL_EVENT_NAMES:
            self._event_sequences[run_id] = 1
            return 1

        run = await self.task_store.get_run(run_id)
        if run:
            for source in (
                await self._read_event_router_events(run),
                self._read_workspace_events(run.workspace_path),
            ):
                sequences.extend(
                    int(event["sequence"])
                    for event in source
                    if isinstance(event.get("sequence"), int)
                )
        next_sequence = (max(sequences) if sequences else 0) + 1
        self._event_sequences[run_id] = next_sequence
        return next_sequence

    async def _write_run_snapshot(self, run: BackgroundRun) -> None:
        task = await self.task_store.get_task(run.task_id)
        if task and not task.workspace_policy.write_run_json:
            return
        workspace = self._resolve_workspace()
        if workspace is None:
            return
        workspace.files.write_text(
            f"{run.workspace_path}/run.json",
            run.model_dump_json(indent=2),
        )

    async def _write_run_event(self, event: dict[str, Any]) -> None:
        workspace_path = event.get("workspace_path")
        if not workspace_path:
            return
        task_id = event.get("task_id")
        if task_id:
            task = await self.task_store.get_task(task_id)
            if task and not task.workspace_policy.write_events_jsonl:
                return
        workspace = self._resolve_workspace()
        if workspace is None:
            return
        workspace.files.append_text(
            f"{workspace_path}/events.jsonl",
            json.dumps(event, default=str),
        )

    async def _append_event_router(self, event: dict[str, Any]) -> None:
        if self.event_router is None:
            return
        try:
            from omnicoreagent.core.events.base import Event, EventType

            await asyncio.wait_for(
                self.event_router.append(
                    session_id=event.get("session_id") or event.get("run_id"),
                    event=Event(
                        type=EventType.BACKGROUND_AGENT_STATUS,
                        payload={
                            "agent_id": event.get("agent_id") or "background",
                            "status": event["event"],
                            "event": event["event"],
                            "timestamp": event["timestamp"],
                            "session_id": event.get("session_id"),
                            "task_id": event.get("task_id"),
                            "run_id": event.get("run_id"),
                            "run_status": event.get("status"),
                            "attempt": event.get("attempt"),
                            "sequence": event.get("sequence"),
                            "workspace_path": event.get("workspace_path"),
                            "last_run": event.get("run_id"),
                            "run_count": event.get("sequence"),
                            "error": event.get("error"),
                        },
                        agent_name=event.get("agent_id") or "background",
                    ),
                ),
                timeout=self._event_append_timeout_seconds,
            )
        except Exception:
            return

    async def _read_event_router_events(self, run: BackgroundRun) -> list[dict[str, Any]]:
        if self.event_router is None:
            return []
        try:
            router_events = await asyncio.wait_for(
                self.event_router.get_events(session_id=run.session_id),
                timeout=self._event_replay_timeout_seconds,
            )
        except Exception:
            return []

        events: list[dict[str, Any]] = []
        for item in router_events:
            raw = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            payload = raw.get("payload", {})
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump()
            if payload.get("run_id") != run.run_id and payload.get("last_run") != run.run_id:
                continue
            event = {
                "event": payload.get("event") or payload.get("status"),
                "timestamp": payload.get("timestamp") or raw.get("timestamp"),
                "agent_id": payload.get("agent_id"),
                "task_id": payload.get("task_id"),
                "run_id": payload.get("run_id") or payload.get("last_run"),
                "session_id": payload.get("session_id"),
                "status": payload.get("run_status"),
                "attempt": payload.get("attempt"),
                "sequence": payload.get("sequence") or payload.get("run_count"),
                "workspace_path": payload.get("workspace_path"),
            }
            if payload.get("error"):
                event["error"] = payload["error"]
            events.append({key: value for key, value in event.items() if value is not None})

        return sorted(
            events,
            key=lambda event: (
                event.get("sequence", 0),
                event.get("timestamp", ""),
            ),
        )

    @staticmethod
    def _prepare_event_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []

        normalized: list[dict[str, Any]] = []
        seen_sequences: set[int] = set()
        for event in events:
            sequence = event.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                return []
            if sequence < 1 or sequence in seen_sequences:
                return []
            seen_sequences.add(sequence)
            normalized_event = dict(event)
            normalized_event["sequence"] = sequence
            normalized.append(normalized_event)

        normalized = sorted(
            normalized,
            key=lambda event: (
                event.get("sequence", 0),
                event.get("timestamp", ""),
            ),
        )
        if [event["sequence"] for event in normalized] != list(
            range(1, len(normalized) + 1)
        ):
            return []
        return normalized

    def _resolve_workspace(self) -> Any | None:
        if self._workspace is not None:
            return self._workspace
        try:
            from omnicoreagent.core.workspace.manager import Workspace

            self._workspace = Workspace.from_config().ensure()
        except Exception:
            self._workspace = None
        return self._workspace

    def _read_workspace_events(self, workspace_path: str) -> list[dict[str, Any]]:
        workspace = self._resolve_workspace()
        if workspace is None:
            return []
        try:
            content = workspace.files.read_text(f"{workspace_path}/events.jsonl")
        except Exception:
            return []
        events: list[dict[str, Any]] = []
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _result_preview(result: Any) -> str:
        if isinstance(result, dict) and "response" in result:
            return str(result["response"])[:1000]
        return str(result)[:1000]

    @staticmethod
    def _safe_model_config(agent: Any) -> dict[str, Any] | None:
        value = getattr(agent, "model_config", None)
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value if isinstance(value, dict) else None

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _safe_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            safe_values = []
            for item in value:
                if hasattr(item, "model_dump"):
                    safe_values.append(item.model_dump())
                elif isinstance(item, dict):
                    safe_values.append(item)
            return safe_values
        return []

    @staticmethod
    def _safe_workspace_config(agent: Any) -> dict[str, Any] | None:
        config = getattr(agent, "agent_config", None)
        if hasattr(config, "model_dump"):
            config = config.model_dump()
        if isinstance(config, dict):
            workspace_config = config.get("workspace_config")
            if hasattr(workspace_config, "model_dump"):
                return workspace_config.model_dump()
            if isinstance(workspace_config, dict):
                return workspace_config
        return None
