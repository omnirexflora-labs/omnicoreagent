"""Schedule dispatch service for background execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.models import BackgroundRun
from omnicoreagent.background.models import RunStatus, TriggerType, next_schedule_due, utc_now
from omnicoreagent.background.run_helpers import build_run
from omnicoreagent.background.store.base import AbstractTaskStore


class BackgroundScheduleDispatcher:
    """Converts due schedule state into durable queued run records."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        event_log: BackgroundEventLog,
        emit_run: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.task_store = task_store
        self.event_log = event_log
        self._emit_run = emit_run

    async def emit_run(self, event_name: str, run: BackgroundRun) -> None:
        if self._emit_run is not None:
            await self._emit_run(event_name, run)
            return
        await self.event_log.emit_run(event_name, run)

    async def dispatch_due_schedules(self, limit: int = 25) -> bool:
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
            await self.emit_run("background_task_scheduled", created)
            await self.emit_run(
                "background_run_skipped"
                if created.status == RunStatus.SKIPPED
                else "background_run_queued",
                created,
            )
        return dispatched_any
