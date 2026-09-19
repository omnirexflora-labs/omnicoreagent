"""Durable run state: what a run has done, saved as it goes.

Each run keeps a record in the memory store the application chose (in memory,
SQL, Redis, or MongoDB): its status, step, usage, trace, and the state of each
tool call. Messages are not copied; they are in the session history. A tool
call is recorded as ``started`` before it runs and ``completed`` after, so a
run that stops part-way shows exactly which calls may have had an effect.

Records are versioned: a save names the version it read, and a save from a
stale reader is refused (``RunStateConflict``), so two workers cannot both
advance one run.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from omnicoreagent.core.logging import logger
from omnicoreagent.governance.hashing import arguments_digest

RUN_STATUSES = (
    "running",
    "awaiting_approval",
    "interrupted",
    "completed",
    "blocked",
    "failed",
    "cancelled",
)

_CURRENT: ContextVar["RunTracker | None"] = ContextVar("omnicoreagent_run", default=None)


class RunStateConflict(Exception):
    """A run record changed since it was read, or already exists."""


class RunStateUnsupported(NotImplementedError):
    """The memory store does not keep run state."""


def current_run() -> "RunTracker | None":
    """The tracker of the run executing in this context, if any."""
    return _CURRENT.get()


def supports_run_state(store: Any) -> bool:
    """Whether a memory store or router can keep run records."""
    return all(
        inspect.iscoroutinefunction(getattr(store, name, None))
        for name in ("save_run_state", "get_run_state", "list_run_states")
    )




def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunTracker:
    """Saves one run's record as the run progresses."""

    def __init__(
        self,
        store: Any,
        *,
        run_id: str,
        session_id: str,
        agent_name: str,
        agent_version: str | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        # A store (or router) without async run-state methods, such as one
        # written before durable runs, keeps working; its runs are not durable.
        self.enabled = supports_run_state(store)
        self._version: int | None = None
        self._lock = asyncio.Lock()
        self.record: dict[str, Any] = {
            "run_id": run_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "agent_version": agent_version,
            "status": "running",
            "step": 0,
            "trace_ids": [],
            "tool_calls": [],
            "usage": {},
            # The run's own working context: the session history exactly as
            # this run loaded it, and the messages this run added. Session
            # history is shared with other requests and can be windowed or
            # summarized by them; this is not. Stored as history stores it
            # (the same privacy redaction).
            "context": {"history": None, "messages": []},
            # Approvals asked for during this run and what a person decided.
            "approvals": [],
            "error": None,
            "created_at": _now(),
            "updated_at": None,
        }

    async def _save(self) -> None:
        if not self.enabled:
            return
        self.record["updated_at"] = _now()
        try:
            self._version = await self.store.save_run_state(
                dict(self.record), expected_version=self._version
            )
        except RunStateUnsupported:
            # A custom memory store without run state: the run still works,
            # it is just not durable.
            self.enabled = False
            logger.debug(f"Run state not kept for {self.run_id}: the memory store has none")

    async def start(self, trace_id: str | None) -> None:
        async with self._lock:
            if trace_id:
                self.record["trace_ids"].append(trace_id)
            await self._save()

    async def step(self, number: int) -> None:
        async with self._lock:
            self.record["step"] = number
            await self._save()

    async def set_history(self, messages: list[dict[str, Any]]) -> None:
        """Keep the history this run started from (only the first load counts)."""
        async with self._lock:
            if self.record["context"]["history"] is not None:
                return
            self.record["context"]["history"] = [dict(m) for m in messages]
            await self._save()

    async def add_message(self, message: dict[str, Any]) -> None:
        async with self._lock:
            self.record["context"]["messages"].append(dict(message))
            await self._save()

    async def tool_started(
        self, *, tool_call_id: str, tool_name: str, provider: str | None, arguments: Any
    ) -> None:
        async with self._lock:
            entry = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "provider": provider,
                "arguments_digest": arguments_digest(arguments),
                "step": self.record["step"],
                "state": "started",
                "outcome": None,
                "started_at": _now(),
                "ended_at": None,
            }
            calls = [c for c in self.record["tool_calls"] if c["tool_call_id"] != tool_call_id]
            self.record["tool_calls"] = [*calls, entry]
            await self._save()

    async def tool_finished(
        self, *, tool_call_id: str, outcome: str, state: str = "completed"
    ) -> None:
        async with self._lock:
            for call in self.record["tool_calls"]:
                if call["tool_call_id"] == tool_call_id:
                    call.update(state=state, outcome=outcome, ended_at=_now())
            await self._save()

    async def finish(
        self, status: str, *, usage: Any = None, error: BaseException | None = None
    ) -> None:
        async with self._lock:
            self.record["status"] = status
            if usage is not None:
                self.record["usage"] = _usage_dict(usage)
            if error is not None:
                self.record["error"] = {"type": type(error).__name__, "message": str(error)}
            await self._save()

    async def add_approval(self, approval: dict[str, Any]) -> None:
        async with self._lock:
            self.record.setdefault("approvals", []).append(dict(approval))
            await self._save()

    async def update_approval(self, approval_id: str, **fields: Any) -> None:
        async with self._lock:
            for approval in self.record.setdefault("approvals", []):
                if approval["approval_id"] == approval_id:
                    approval.update(fields)
            await self._save()

    async def reload(self) -> None:
        """Take the stored record as current (after someone else changed it)."""
        async with self._lock:
            stored = await self.load()
            if stored is not None:
                self._version = stored.pop("version")
                self.record = stored

    async def load(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        return await self.store.get_run_state(self.run_id)

    @asynccontextmanager
    async def active(self):
        """Make this the current run for code running inside it."""
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)


def _usage_dict(usage: Any) -> dict[str, Any]:
    if isinstance(usage, dict):
        return dict(usage)
    fields = ("requests", "request_tokens", "response_tokens", "total_tokens")
    return {name: getattr(usage, name, None) for name in fields if hasattr(usage, name)}
