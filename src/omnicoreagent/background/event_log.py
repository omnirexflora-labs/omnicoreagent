"""Background run event persistence and runtime event routing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from omnicoreagent.background.models import (
    INITIAL_EVENT_NAMES,
    TERMINAL_EVENT_NAMES,
    BackgroundRun,
)
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.workspace_io import BackgroundWorkspaceIO


class BackgroundEventLog:
    """Owns background lifecycle event ordering, storage, and event-router fanout."""

    def __init__(
        self,
        *,
        task_store: AbstractTaskStore,
        workspace_io: BackgroundWorkspaceIO,
        event_router: Any = None,
        replay_timeout_seconds: float = 2.0,
        append_timeout_seconds: float = 2.0,
    ) -> None:
        self.task_store = task_store
        self.workspace_io = workspace_io
        self.event_router = event_router
        self.replay_timeout_seconds = replay_timeout_seconds
        self.append_timeout_seconds = append_timeout_seconds
        self.local_events: dict[str, list[dict[str, Any]]] = {}
        self.event_sequences: dict[str, int] = {}
        self.router_tasks: set[asyncio.Task] = set()

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
        if event_name in INITIAL_EVENT_NAMES:
            try:
                await self.write_run_event(event)
            except Exception:
                pass
            if self.event_router is not None:
                self.schedule_router_task(self.append_event_router(event), event)
            return
        self.schedule_router_task(self.write_and_route_event(event), event)

    async def get_run_events(self, run: BackgroundRun | None) -> list[dict[str, Any]]:
        if not run:
            return []
        await self.drain_router_tasks(run.run_id)
        events = self.prepare_event_trace(self.local_events.get(run.run_id) or [])
        workspace_events = self.prepare_event_trace(
            self.workspace_io.read_events(run.workspace_path)
        )
        router_events = self.prepare_event_trace(
            await self.read_event_router_events(run)
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

    async def write_and_route_event(self, event: dict[str, Any]) -> None:
        try:
            await self.write_run_event(event)
        except Exception:
            pass
        await self.append_event_router(event)

    def schedule_router_task(self, coroutine, event: dict[str, Any]) -> None:
        task = asyncio.create_task(coroutine)
        task._omnicoreagent_run_id = event.get("run_id")  # type: ignore[attr-defined]
        self.router_tasks.add(task)
        task.add_done_callback(self.router_tasks.discard)

    async def drain_router_tasks(self, run_id: str) -> None:
        pending = [
            task
            for task in self.router_tasks
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

    async def cancel_router_tasks(self) -> None:
        pending = [task for task in self.router_tasks if not task.done()]
        if not pending:
            self.router_tasks.clear()
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.router_tasks.clear()

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
            for source in (
                await self.read_event_router_events(run),
                self.workspace_io.read_events(run.workspace_path),
            ):
                sequences.extend(
                    int(event["sequence"])
                    for event in source
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

    async def append_event_router(self, event: dict[str, Any]) -> None:
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
                            "worker_id": event.get("worker_id"),
                            "lease_generation": event.get("lease_generation"),
                            "heartbeat_at": event.get("heartbeat_at"),
                            "lease_expires_at": event.get("lease_expires_at"),
                            "occurrence_id": event.get("occurrence_id"),
                            "due_at": event.get("due_at"),
                        },
                        agent_name=event.get("agent_id") or "background",
                    ),
                ),
                timeout=self.append_timeout_seconds,
            )
        except Exception:
            return

    async def read_event_router_events(self, run: BackgroundRun) -> list[dict[str, Any]]:
        if self.event_router is None:
            return []
        try:
            router_events = await asyncio.wait_for(
                self.event_router.get_events(session_id=run.session_id),
                timeout=self.replay_timeout_seconds,
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
                "worker_id": payload.get("worker_id"),
                "lease_generation": payload.get("lease_generation"),
                "heartbeat_at": payload.get("heartbeat_at"),
                "lease_expires_at": payload.get("lease_expires_at"),
                "occurrence_id": payload.get("occurrence_id"),
                "due_at": payload.get("due_at"),
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
