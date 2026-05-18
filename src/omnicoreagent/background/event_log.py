"""Background run lifecycle event persistence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from omnicoreagent.background.models import (
    INITIAL_EVENT_NAMES,
    TERMINAL_EVENT_NAMES,
    BackgroundRun,
    RunStatus,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.workspace_io import BackgroundWorkspaceIO
from omnicoreagent.core.telemetry import (
    AbstractTelemetryStore,
    ActorType,
    SpanStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TraceStatus,
)
from omnicoreagent.core.telemetry.models import utc_now


class BackgroundEventLog:
    """Owns background lifecycle event ordering and workspace-backed replay."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        workspace_io: BackgroundWorkspaceIO,
        telemetry_store: AbstractTelemetryStore | None = None,
        replay_timeout_seconds: float = 2.0,
        append_timeout_seconds: float = 2.0,
    ) -> None:
        self.task_store = task_store
        self.workspace_io = workspace_io
        self.telemetry_store = telemetry_store
        self.replay_timeout_seconds = replay_timeout_seconds
        self.append_timeout_seconds = append_timeout_seconds
        self.local_events: dict[str, list[dict[str, Any]]] = {}
        self.event_sequences: dict[str, int] = {}
        self.event_tasks: set[asyncio.Task] = set()
        self._telemetry_traces: set[str] = set()

    async def emit_run(
        self, event_name: str, run: BackgroundRun, **extra_payload: Any
    ) -> None:
        try:
            await self.emit(
                event_name,
                agent_id=run.agent_id,
                task_id=run.task_id,
                run_id=run.run_id,
                session_id=run.session_id,
                status=run.status.value,
                attempt=run.attempt,
                workspace_path=run.workspace_path,
                worker_id=run.lease_owner,
                lease_generation=run.lease_generation,
                heartbeat_at=run.heartbeat_at.isoformat() if run.heartbeat_at else None,
                lease_expires_at=(
                    run.lease_expires_at.isoformat() if run.lease_expires_at else None
                ),
                occurrence_id=run.occurrence_id,
                due_at=run.due_at.isoformat() if run.due_at else None,
                **extra_payload,
            )
        except Exception:
            pass
        if event_name != "background_run_heartbeat":
            try:
                await self.write_run_snapshot(run)
            except Exception:
                pass

    async def emit(self, event_name: str, **payload: Any) -> None:
        run_id = payload.get("run_id")
        event = {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if not run_id:
            return

        events = self.local_events.setdefault(run_id, [])
        event["sequence"] = await self.next_run_event_sequence(
            run_id, event_name, events
        )
        events.append(event)
        await self.append_telemetry_event(event_name, event)
        if event_name in INITIAL_EVENT_NAMES:
            try:
                await self.write_run_event(event)
            except Exception:
                pass
            return
        self.schedule_event_task(self.write_run_event(event), event)

    async def get_run_events(self, run: BackgroundRun | None) -> list[dict[str, Any]]:
        if not run:
            return []
        await self.drain_event_tasks(run.run_id)
        events = self.prepare_event_trace(self.local_events.get(run.run_id) or [])
        workspace_events = self.prepare_event_trace(
            self.workspace_io.read_events(run.workspace_path)
        )
        candidates = [events, workspace_events]
        complete = [
            candidate
            for candidate in candidates
            if candidate and candidate[-1].get("event") in TERMINAL_EVENT_NAMES
        ]
        if complete:
            return max(complete, key=len)
        if events:
            return events
        return workspace_events

    def schedule_event_task(self, coroutine, event: dict[str, Any]) -> None:
        task = asyncio.create_task(coroutine)
        task._omnicoreagent_run_id = event.get("run_id")  # type: ignore[attr-defined]
        self.event_tasks.add(task)
        task.add_done_callback(self.event_tasks.discard)

    async def drain_event_tasks(self, run_id: str) -> None:
        pending = [
            task
            for task in self.event_tasks
            if not task.done()
            and getattr(task, "_omnicoreagent_run_id", None) == run_id
        ]
        if not pending:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.gather(*pending, return_exceptions=True)),
                timeout=self.replay_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return

    async def cancel_event_tasks(self) -> None:
        pending = [task for task in self.event_tasks if not task.done()]
        if not pending:
            self.event_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.event_tasks.clear()

    async def next_run_event_sequence(
        self, run_id: str, event_name: str, local_events: list[dict[str, Any]]
    ) -> int:
        cached = self.event_sequences.get(run_id)
        if cached is not None:
            self.event_sequences[run_id] = cached + 1
            return cached + 1
        sequences = [
            int(event["sequence"])
            for event in local_events
            if isinstance(event.get("sequence"), int)
        ]
        if sequences:
            next_sequence = max(sequences) + 1
            self.event_sequences[run_id] = next_sequence
            return next_sequence
        if event_name in INITIAL_EVENT_NAMES:
            self.event_sequences[run_id] = 1
            return 1

        run = await self.task_store.get_run(run_id)
        if run:
            sequences.extend(
                int(event["sequence"])
                for event in self.workspace_io.read_events(run.workspace_path)
                if isinstance(event.get("sequence"), int)
            )
        next_sequence = (max(sequences) if sequences else 0) + 1
        self.event_sequences[run_id] = next_sequence
        return next_sequence

    async def write_run_snapshot(self, run: BackgroundRun) -> None:
        task = await self.task_store.get_task(run.task_id)
        if task and not task.workspace_policy.write_run_json:
            return
        self.workspace_io.write_run_snapshot(run)

    async def write_run_event(self, event: dict[str, Any]) -> None:
        task_id = event.get("task_id")
        if task_id:
            task = await self.task_store.get_task(task_id)
            if task and not task.workspace_policy.write_events_jsonl:
                return
        self.workspace_io.append_event(event)

    async def append_telemetry_event(
        self,
        event_name: str,
        event: dict[str, Any],
    ) -> None:
        if self.telemetry_store is None:
            return
        try:
            await asyncio.wait_for(
                self._append_telemetry_event(event_name, event),
                timeout=self.append_timeout_seconds,
            )
        except Exception:
            return

    async def _append_telemetry_event(
        self,
        event_name: str,
        event: dict[str, Any],
    ) -> None:
        if self.telemetry_store is None:
            return
        run_id = str(event["run_id"])
        trace_id = self._telemetry_trace_id(run_id)
        span_id = self._telemetry_span_id(run_id)
        await self._ensure_telemetry_trace(trace_id, span_id, event)
        await self.telemetry_store.append_event(
            trace_id,
            TelemetryEvent(
                trace_id=trace_id,
                span_id=span_id,
                event_type=event_name,
                actor=TelemetryActor(
                    type=ActorType.BACKGROUND,
                    id=event.get("agent_id"),
                    name=event.get("agent_id"),
                ),
                output={"event": event_name, "status": event.get("status")},
                metadata=dict(event),
            ),
        )
        if event_name in TERMINAL_EVENT_NAMES:
            await self._finish_telemetry_trace(trace_id, span_id, event)

    async def _ensure_telemetry_trace(
        self,
        trace_id: str,
        span_id: str,
        event: dict[str, Any],
    ) -> None:
        if self.telemetry_store is None or trace_id in self._telemetry_traces:
            return
        self._telemetry_traces.add(trace_id)
        await self.telemetry_store.upsert_trace(
            TelemetryTrace(
                trace_id=trace_id,
                root_span_id=span_id,
                run_id=event.get("run_id"),
                session_id=event.get("session_id"),
                task_id=event.get("task_id"),
                agent_id=event.get("agent_id"),
                metadata=TelemetryTraceMetadata(
                    agent_name=event.get("agent_id"),
                    tags=["background"],
                ),
                spans=[
                    TelemetrySpan(
                        trace_id=trace_id,
                        span_id=span_id,
                        name="background.run",
                        kind="background.run",
                        actor=TelemetryActor(
                            type=ActorType.BACKGROUND,
                            id=event.get("agent_id"),
                            name=event.get("agent_id"),
                        ),
                        input={
                            "run_id": event.get("run_id"),
                            "task_id": event.get("task_id"),
                            "trigger": event.get("trigger"),
                        },
                        attributes={
                            "worker_id": event.get("worker_id"),
                            "workspace_path": event.get("workspace_path"),
                        },
                    )
                ],
            )
        )

    async def _finish_telemetry_trace(
        self,
        trace_id: str,
        span_id: str,
        event: dict[str, Any],
    ) -> None:
        if self.telemetry_store is None:
            return
        ended_at = utc_now()
        await self.telemetry_store.end_span(
            trace_id,
            span_id,
            {
                "status": self._span_status_for_event(event).value,
                "ended_at": ended_at,
                "output": {
                    "status": event.get("status"),
                    "error": event.get("error"),
                    "result_preview": event.get("result_preview"),
                },
            },
        )
        await self.telemetry_store.update_trace(
            trace_id,
            {"status": self._trace_status_for_event(event).value, "ended_at": ended_at},
        )

    @staticmethod
    def _trace_status_for_event(event: dict[str, Any]) -> TraceStatus:
        status = event.get("status")
        if status == RunStatus.FAILED.value:
            return TraceStatus.FAILED
        if status == RunStatus.CANCELLED.value:
            return TraceStatus.CANCELLED
        if status == RunStatus.TIMEOUT.value:
            return TraceStatus.TIMEOUT
        return TraceStatus.COMPLETED

    @staticmethod
    def _span_status_for_event(event: dict[str, Any]) -> SpanStatus:
        status = event.get("status")
        if status == RunStatus.FAILED.value:
            return SpanStatus.ERROR
        if status == RunStatus.CANCELLED.value:
            return SpanStatus.CANCELLED
        if status == RunStatus.TIMEOUT.value:
            return SpanStatus.TIMEOUT
        if status == RunStatus.SKIPPED.value:
            return SpanStatus.SKIPPED
        return SpanStatus.OK

    @staticmethod
    def _telemetry_trace_id(run_id: str) -> str:
        return f"trace_background_{run_id}"

    @staticmethod
    def _telemetry_span_id(run_id: str) -> str:
        return f"span_background_{run_id}"

    @staticmethod
    def prepare_event_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
