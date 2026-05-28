"""Schedule dispatch service for background execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.models import (
    BackgroundRun,
    RunStatus,
    TriggerType,
    build_occurrence_id,
    schedule_due_occurrences,
    utc_now,
)
from omnicoreagent.background.run_helpers import build_run
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.governance.capabilities import background_run_authority_request
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.governance.snapshots import (
    POLICY_SNAPSHOT_METADATA_KEY,
    attach_policy_snapshot,
    require_current_policy_snapshot,
)


class BackgroundScheduleDispatcher:
    """Converts due schedule state into durable queued run records."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        event_log: BackgroundEventLog,
        governance_engine=None,
        emit_run: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.task_store = task_store
        self.event_log = event_log
        self.governance_engine = governance_engine
        self._emit_run = emit_run

    async def emit_run(self, event_name: str, run: BackgroundRun) -> None:
        if self._emit_run is not None:
            await self._emit_run(event_name, run)
            return
        await self.event_log.emit_run(event_name, run)

    async def dispatch_due_schedules(self, limit: int = 25) -> bool:
        dispatched_any = False
        remaining = max(limit, 0)
        now = utc_now()
        due_items = await self.task_store.get_due_schedules(now, limit=remaining)
        for task, state, occurrence_id in due_items:
            if remaining <= 0 or state.next_due_at is None:
                continue
            trigger = TriggerType(task.schedule.type.value)
            occurrences, final_next_due_at = schedule_due_occurrences(
                task.schedule,
                state.next_due_at,
                now,
                limit=remaining,
            )
            if not occurrences:
                continue
            for index, due_at in enumerate(occurrences):
                occurrence_for_due = (
                    occurrence_id
                    if index == 0
                    else build_occurrence_id(
                        task.schedule.type, state.schedule_revision, due_at
                    )
                )
                run = build_run(
                    task,
                    trigger,
                    task.query,
                    due_at=due_at,
                    occurrence_id=occurrence_for_due,
                )
                try:
                    task, run = await self._authorize_scheduled_run(task=task, run=run)
                except GovernanceError:
                    await self.task_store.set_schedule_paused(task.task_id, True)
                    raise
                existing_run_ids = {
                    item.run_id for item in await self.task_store.list_runs(task.task_id)
                }
                next_due_at = (
                    occurrences[index + 1]
                    if index + 1 < len(occurrences)
                    else final_next_due_at
                )
                created = await self.task_store.dispatch_scheduled_run(
                    run,
                    task.overlap_policy,
                    state.schedule_revision,
                    next_due_at,
                )
                dispatched_any = True
                remaining -= 1
                if created.run_id in existing_run_ids:
                    continue
                await self.emit_run("background_task_scheduled", created)
                await self.emit_run(
                    "background_run_skipped"
                    if created.status == RunStatus.SKIPPED
                    else "background_run_queued",
                    created,
                )
        return dispatched_any

    async def _authorize_scheduled_run(
        self, *, task, run: BackgroundRun
    ) -> tuple[Any, BackgroundRun]:
        if self.governance_engine is None:
            if POLICY_SNAPSHOT_METADATA_KEY in task.metadata:
                require_current_policy_snapshot(
                    task.metadata,
                    None,
                    surface=f"background task {task.task_id}",
                )
            return task, run
        require_current_policy_snapshot(
            task.metadata,
            self.governance_engine,
            surface=f"background task {task.task_id}",
            required=True,
        )
        await self.governance_engine.authorize(
            background_run_authority_request(
                action="start",
                run=run,
                task=task,
                actor="background_scheduler",
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
        return task, run.model_copy(
            update={
                "metadata": {
                    **attach_policy_snapshot(run.metadata, self.governance_engine),
                    "governance_run_start_authorized": True,
                }
            }
        )
