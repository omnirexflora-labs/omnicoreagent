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
from uuid import uuid4

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


class RunSuspended(Exception):
    """The run is waiting for a person to decide one or more approvals."""

    def __init__(self, approvals: list[dict[str, Any]]):
        super().__init__(f"Run is waiting for {len(approvals)} approval(s)")
        self.approvals = approvals


class RunInterrupted(Exception):
    """Someone asked the run to stop at its next step boundary."""


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
        lease_seconds: int = 60,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.lease_seconds = lease_seconds
        # This process's claim on the run; a recovered run gets a new owner.
        self.owner = f"owner_{uuid4().hex}"
        # The owner stored when this tracker loaded the record (a resume),
        # until this tracker's first save replaces it.
        self._loaded_owner: str | None = None
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
            "owner": None,
            "heartbeat_at": None,
            "lease_seconds": lease_seconds,
            "attempt": 1,
            "previous_attempts": [],
            # Written by others while the run is live: steering messages and
            # a request to stop at the next step.
            "inbox": [],
            "interrupt_requested": False,
            # Whether the run opened a sandbox (a resumed run is told it was reset).
            "sandbox_used": False,
            # Programs (run_code) paused waiting for a person, signed.
            "code_programs": {},
            "created_at": _now(),
            "updated_at": None,
        }

    @classmethod
    def from_record(
        cls, store: Any, record: dict[str, Any], *, lease_seconds: int = 60
    ) -> "RunTracker":
        """Continue a stored run (a resume or a recovery); the next save must
        match its version, so two processes cannot both take it over."""
        record = dict(record)
        tracker = cls(
            store,
            run_id=record["run_id"],
            session_id=record["session_id"],
            agent_name=record["agent_name"],
            agent_version=record.get("agent_version"),
            lease_seconds=lease_seconds,
        )
        tracker._version = record.pop("version")
        tracker._loaded_owner = record.get("owner")
        tracker.record = {
            "attempt": 1,
            "previous_attempts": [],
            "inbox": [],
            **record,
            "status": "running",
            "lease_seconds": lease_seconds,
            "interrupt_requested": False,
        }
        return tracker

    @classmethod
    def new_attempt(
        cls, store: Any, record: dict[str, Any], *, lease_seconds: int = 60
    ) -> "RunTracker":
        """Start a finished or failed run again (a retry with the same run ID),
        keeping a summary of the earlier attempts."""
        tracker = cls.from_record(store, record, lease_seconds=lease_seconds)
        earlier = tracker.record
        tracker.record = {
            **earlier,
            "attempt": int(earlier.get("attempt") or 1) + 1,
            "previous_attempts": [
                *earlier.get("previous_attempts", []),
                {
                    "attempt": earlier.get("attempt") or 1,
                    "status": record["status"],
                    "error": earlier.get("error"),
                    "step": earlier.get("step"),
                    "trace_ids": list(earlier.get("trace_ids") or []),
                },
            ],
            "step": 0,
            "tool_calls": [],
            "approvals": [],
            "context": {"history": None, "messages": []},
            "usage": {},
            "error": None,
        }
        return tracker

    async def _save(self) -> None:
        if not self.enabled:
            return
        self.record["updated_at"] = _now()
        if self.record["status"] == "running":
            self.record["owner"] = self.owner
            self.record["heartbeat_at"] = self.record["updated_at"]
        try:
            for _ in range(5):
                try:
                    self._version = await self.store.save_run_state(
                        dict(self.record), expected_version=self._version
                    )
                    return
                except RunStateConflict:
                    # Someone else wrote the record (a steering message, an
                    # interrupt). Take their fields and save again, unless the
                    # run now belongs to another process.
                    if not await self._merge_external():
                        raise
            raise RunStateConflict(f"Run {self.run_id} keeps changing; could not save")
        except RunStateUnsupported:
            # A custom memory store without run state: the run still works,
            # it is just not durable.
            self.enabled = False
            logger.debug(f"Run state not kept for {self.run_id}: the memory store has none")

    async def _merge_external(self) -> bool:
        """Merge fields other writers own into this record; False on takeover."""
        if self._version is None:
            return False  # creating: the record exists, nothing to merge
        stored = await self.store.get_run_state(self.run_id)
        if stored is None or stored.get("owner") not in {None, self.owner, self._loaded_owner}:
            return False
        mine = {m["id"]: m for m in self.record.get("inbox", [])}
        merged = []
        for message in stored.get("inbox", []):
            own = mine.pop(message["id"], None)
            # Once delivered here, this process's copy (text removed) wins.
            merged.append(own if own is not None and own.get("delivered") else message)
        merged.extend(mine.values())
        self.record["inbox"] = merged
        self.record["interrupt_requested"] = bool(
            stored.get("interrupt_requested") or self.record.get("interrupt_requested")
        )
        self._version = stored["version"]
        return True

    async def check_external(self) -> tuple[list[dict[str, Any]], bool]:
        """At a step boundary: messages steered to this run, and whether it was
        asked to stop. Delivered messages are marked so they arrive once."""
        if not self.enabled:
            return [], False
        async with self._lock:
            stored = await self.store.get_run_state(self.run_id)
            if stored is None:
                return [], False
            if stored["version"] != self._version:
                await self._merge_external()
            waiting = [m for m in self.record.get("inbox", []) if not m.get("delivered")]
            delivered = [dict(m) for m in waiting]
            for message in waiting:
                # The text now lives in the run's history (redacted as history
                # is); the inbox keeps only a digest of it.
                message["delivered"] = True
                message["delivered_at"] = _now()
                message["content_digest"] = arguments_digest(message.get("content"))
                message["content"] = None
            if waiting:
                await self._save()
            return delivered, bool(self.record.get("interrupt_requested"))

    async def start(self, trace_id: str | None) -> None:
        async with self._lock:
            if trace_id:
                self.record["trace_ids"].append(trace_id)
            await self._save()

    async def save_code_program(self, call_id: str, program: dict[str, Any] | None) -> None:
        """Keep (or drop) a paused program for a `run_code` call."""
        async with self._lock:
            programs = self.record.setdefault("code_programs", {})
            if program is None:
                programs.pop(call_id, None)
            else:
                programs[call_id] = program
            await self._save()

    async def note_sandbox(self) -> None:
        async with self._lock:
            if not self.record.get("sandbox_used"):
                self.record["sandbox_used"] = True
                await self._save()

    async def heartbeat(self) -> None:
        async with self._lock:
            await self._save()

    async def keep_alive(self) -> None:
        """Refresh the heartbeat while the run is live (runs as a task)."""
        interval = max(self.lease_seconds / 3, 0.2)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.heartbeat()
            except RunStateConflict:
                # Another process took the run over; this one must not write.
                logger.warning(f"Run {self.run_id} was taken over by another process")
                return

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
        """Keep a message on the record.

        A tool's result is written at once: the record keeps a completed
        call's state, not its result, so this message is the only place a
        resumed run can get it from. Every other message is followed by a
        save the contract names — the write-ahead before a tool, the next
        step, the finish — before anything can go wrong, and rides on it
        rather than rewriting the whole record on its own. (Decided with the
        maintainer: 11 saves per tool call became 8.)
        """
        async with self._lock:
            self.record["context"]["messages"].append(dict(message))
            if message.get("role") == "tool":
                await self._save()

    async def tool_started(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        provider: str | None,
        arguments: Any,
        parent_tool_call_id: str | None = None,
    ) -> None:
        async with self._lock:
            entry = {
                "tool_call_id": tool_call_id,
                # A call made by a program (run_code) names the run_code call.
                "parent_tool_call_id": parent_tool_call_id,
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
        self,
        status: str,
        *,
        usage: Any = None,
        error: BaseException | None = None,
        budgets: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            self.record["status"] = status
            if usage is not None:
                # A resumed run adds this segment's usage to the earlier ones.
                self.record["usage"] = _add_usage(self.record.get("usage") or {}, _usage_dict(usage))
            if error is not None:
                self.record["error"] = {"type": type(error).__name__, "message": str(error)}
            if budgets:
                # What the run spent, per scope, kept once its own counter is gone.
                self.record["budgets"] = budgets
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

    async def add_budget_request(self, request: dict[str, Any]) -> None:
        async with self._lock:
            self.record.setdefault("budget_requests", []).append(dict(request))
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


async def update_from_outside(store: Any, run_id: str, change) -> dict[str, Any]:
    """Change a run's record from outside the process running it (steering,
    interrupt). ``change(record)`` edits the record; a concurrent save by the
    run is retried against the fresh record."""
    for _ in range(10):
        record = await store.get_run_state(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        version = record.pop("version")
        result = change(record)
        try:
            await store.save_run_state(record, expected_version=version)
            return result
        except RunStateConflict:
            await asyncio.sleep(0.01)
    raise RunStateConflict(f"Run {run_id} keeps changing; try again")


def lease_expired(record: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether a running record's owner has stopped refreshing its heartbeat."""
    heartbeat = record.get("heartbeat_at")
    if not heartbeat:
        return True
    lease = record.get("lease_seconds") or 60
    age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(heartbeat)).total_seconds()
    return age > lease


def _usage_dict(usage: Any) -> dict[str, Any]:
    if isinstance(usage, dict):
        return dict(usage)
    fields = ("requests", "request_tokens", "response_tokens", "total_tokens")
    return {name: getattr(usage, name, None) for name in fields if hasattr(usage, name)}


def _add_usage(before: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    total = dict(before)
    for key, value in segment.items():
        if isinstance(value, (int, float)) and isinstance(total.get(key), (int, float)):
            total[key] = total[key] + value
        elif value is not None:
            total[key] = value
    return total
