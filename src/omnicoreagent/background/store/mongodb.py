"""A durable task store in MongoDB: a document per entity.

Scale plan, S2b. What this replaced was a snapshot store: every mutation took
a lock over the whole store, read all of its state, wrote a complete new
generation of every record, and moved a pointer at it. So one write copied
everything the store had ever kept, and the cost of a write grew with the
history behind it.

A document is an entity now:

    {prefix}_agents {prefix}_tasks {prefix}_schedules {prefix}_runs
    {prefix}_attempts
        _id is the entity's own id; ``data`` is the entity as JSON, beside the
        few fields the store filters, sorts and claims by, and a ``version``.

A write touches the document it writes. MongoDB makes a single document's
update atomic, so a claim is one ``find_one_and_update`` that names the status
and version it expected: exactly one worker wins, with no lock over anything
else. Indexes cover the four questions asked — claim a queued run, a task's
unfinished runs, a scheduled occurrence, and expired leases.

State the snapshot store wrote is imported on first use, so a deployment that
upgrades keeps its agents, tasks, runs and attempts; its collections are left
where they are.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from uuid import uuid4

from omnicoreagent.background.errors import (
    AgentNotFoundError,
    InvalidTaskStoreError,
    RunCancellationRequestedError,
    RunLeaseError,
    RunNotFoundError,
    TaskNotFoundError,
    TaskStoreError,
)
from omnicoreagent.background.models import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundScheduleState,
    BackgroundTaskSpec,
    OverlapPolicy,
    RunStatus,
    ScheduleType,
    build_occurrence_id,
    initial_schedule_due,
)
from omnicoreagent.background.store.base import AbstractTaskStore

T = TypeVar("T")

# How many queued runs a claim looks at before leaving the rest to the next
# poll, and how many times a contended write is tried again.
_CLAIM_CANDIDATES = 50
_RETRIES = 50

_UNFINISHED = {
    RunStatus.QUEUED,
    RunStatus.CLAIMED,
    RunStatus.RUNNING,
    RunStatus.RETRYING,
}
_LEASED = {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}

_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.CLAIMED, RunStatus.CANCELLED, RunStatus.SKIPPED},
    RunStatus.CLAIMED: {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.RUNNING: {
        RunStatus.RETRYING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.AWAITING_BUDGET,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    },
    RunStatus.RETRYING: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.AWAITING_APPROVAL: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.AWAITING_BUDGET: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.TIMEOUT: set(),
    RunStatus.SKIPPED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive(value: datetime | None) -> datetime | None:
    """UTC without a zone: what MongoDB stores and compares."""
    moment = _aware(value)
    return moment.replace(tzinfo=None) if moment is not None else None


def _run_order_key(run: BackgroundRun) -> tuple[datetime, datetime, str]:
    queued_at = run.queued_at or run.triggered_at
    return (queued_at, run.triggered_at, run.run_id)


class MongoDbTaskStore(AbstractTaskStore):
    """Durable MongoDB task store: a document per entity."""

    def __init__(
        self,
        uri: str,
        database: str,
        *,
        collection_prefix: str | None = None,
        connect_timeout: float | None = None,
        lock_timeout: float = 30.0,
        lock_lease_seconds: float = 30.0,
    ) -> None:
        self.uri = uri
        self.database_name = database
        self.collection_prefix = collection_prefix or "omnicoreagent_background"
        self.connect_timeout = connect_timeout
        # Taken for callers that configured the snapshot store's lock; nothing
        # holds a lock over the whole store any more.
        self.lock_timeout = lock_timeout
        self.lock_lease_seconds = lock_lease_seconds
        self._client: Any = None
        self._db: Any = None
        self._closed = False
        self._backend_init_lock = asyncio.Lock()

    # --- plumbing ---------------------------------------------------------

    async def initialize(self) -> None:
        self._closed = False
        if self._db is not None:
            return
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            from pymongo.write_concern import WriteConcern
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise InvalidTaskStoreError(
                "MongoDB task store requires the mongodb extra: "
                "pip install 'omnicoreagent[mongodb]'"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.connect_timeout is not None:
            kwargs["serverSelectionTimeoutMS"] = int(self.connect_timeout * 1000)
        client = AsyncIOMotorClient(self.uri, **kwargs)
        database = client[self.database_name].with_options(
            write_concern=WriteConcern("majority")
        )
        await database.command("ping")
        self._client, self._db = client, database
        await self._ensure_indexes()
        await self._import_snapshot_state()

    async def close(self) -> None:
        self._closed = True
        client, self._client, self._db = self._client, None, None
        if client is not None:
            client.close()

    def _collection(self, name: str):
        if self._db is None:
            raise TaskStoreError("MongoDB task store is not initialized")
        return self._db[f"{self.collection_prefix}_{name}"]

    async def _ready(self, name: str):
        if self._db is None:
            async with self._backend_init_lock:
                if self._db is None:
                    # A manager is given its store before it is initialized and
                    # uses it straight away, so an operation connects.
                    await self.initialize()
        return self._collection(name)

    async def _ensure_indexes(self) -> None:
        """What the store asks for: the claim order, a task's runs, an
        occurrence, and what has aged out."""
        runs = self._collection("runs")
        await runs.create_index([("status", 1), ("queued_at", 1)])
        await runs.create_index([("task_id", 1), ("status", 1)])
        await runs.create_index([("task_id", 1), ("occurrence_id", 1)])
        await runs.create_index([("lease_expires_at", 1)])
        await self._collection("tasks").create_index([("agent_id", 1)])
        await self._collection("attempts").create_index(
            [("run_id", 1), ("attempt_number", 1)]
        )

    @staticmethod
    def _document(model: Any, **fields: Any) -> dict[str, Any]:
        return {"data": model.model_dump_json(), **fields}

    @staticmethod
    def _model(document: dict[str, Any] | None, kind: Any) -> Any | None:
        if not document:
            return None
        return kind.model_validate_json(document["data"])

    async def _put(self, name: str, entity_id: str, document: dict[str, Any]) -> None:
        collection = await self._ready(name)
        await collection.update_one(
            {"_id": entity_id},
            {"$set": document, "$inc": {"version": 1}},
            upsert=True,
        )

    # --- agents -----------------------------------------------------------

    async def save_agent(self, spec: BackgroundAgentSpec) -> None:
        await self._put(
            "agents",
            spec.agent_id,
            self._document(spec, created_at=_naive(getattr(spec, "created_at", None))),
        )

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        agents = await self._ready("agents")
        return self._model(await agents.find_one({"_id": agent_id}), BackgroundAgentSpec)

    async def delete_agent(self, agent_id: str) -> None:
        agents = await self._ready("agents")
        await agents.delete_one({"_id": agent_id})

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        agents = await self._ready("agents")
        found = [
            self._model(document, BackgroundAgentSpec)
            async for document in agents.find().sort("created_at", 1)
        ]
        return self._sorted([agent for agent in found if agent is not None])

    # --- tasks ------------------------------------------------------------

    async def save_task(self, spec: BackgroundTaskSpec) -> None:
        if await self.get_agent(spec.agent_id) is None:
            raise AgentNotFoundError(f"Agent not found: {spec.agent_id}")
        tasks = await self._ready("tasks")
        schedules = await self._ready("schedules")
        existing = self._model(
            await tasks.find_one({"_id": spec.task_id}), BackgroundTaskSpec
        )
        await self._put(
            "tasks",
            spec.task_id,
            self._document(
                spec,
                agent_id=spec.agent_id,
                enabled=bool(spec.enabled),
                created_at=_naive(getattr(spec, "created_at", None)),
            ),
        )
        if existing is not None and existing.schedule == spec.schedule:
            return
        current = self._model(
            await schedules.find_one({"_id": spec.task_id}), BackgroundScheduleState
        )
        revision = (current.schedule_revision + 1) if (existing and current) else 1
        await self._write_schedule(
            BackgroundScheduleState(
                task_id=spec.task_id,
                next_due_at=initial_schedule_due(spec.schedule, _now()),
                schedule_revision=revision,
            )
        )

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        tasks = await self._ready("tasks")
        return self._model(await tasks.find_one({"_id": task_id}), BackgroundTaskSpec)

    async def delete_task(self, task_id: str) -> None:
        tasks = await self._ready("tasks")
        schedules = await self._ready("schedules")
        await tasks.delete_one({"_id": task_id})
        await schedules.delete_one({"_id": task_id})

    async def delete_runs_for_task(self, task_id: str) -> None:
        runs = await self._ready("runs")
        attempts = await self._ready("attempts")
        run_ids = [document["_id"] async for document in runs.find({"task_id": task_id})]
        if run_ids:
            await attempts.delete_many({"run_id": {"$in": run_ids}})
        await runs.delete_many({"task_id": task_id})

    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]:
        tasks = await self._ready("tasks")
        query: dict[str, Any] = {}
        if agent_id is not None:
            query["agent_id"] = agent_id
        if enabled is not None:
            query["enabled"] = bool(enabled)
        found = [
            self._model(document, BackgroundTaskSpec)
            async for document in tasks.find(query).sort("created_at", 1)
        ]
        return self._sorted([task for task in found if task is not None])

    # --- schedules --------------------------------------------------------

    async def _write_schedule(self, state: BackgroundScheduleState) -> None:
        await self._put(
            "schedules",
            state.task_id,
            self._document(
                state,
                next_due_at=_naive(state.next_due_at),
                paused=bool(state.paused),
                schedule_revision=state.schedule_revision,
            ),
        )

    async def save_schedule_state(self, state: BackgroundScheduleState) -> None:
        if await self.get_task(state.task_id) is None:
            raise TaskNotFoundError(f"Task not found: {state.task_id}")
        await self._write_schedule(state)

    async def set_schedule_paused(
        self, task_id: str, paused: bool, *, reason: str | None = None
    ) -> BackgroundScheduleState:
        def change(state: BackgroundScheduleState) -> BackgroundScheduleState:
            return state.model_copy(
                update={
                    "paused": paused,
                    "paused_reason": reason if paused else None,
                    "updated_at": _now(),
                },
                deep=True,
            )

        return await self._change_schedule(task_id, change)

    async def get_schedule_state(self, task_id: str) -> BackgroundScheduleState | None:
        schedules = await self._ready("schedules")
        return self._model(
            await schedules.find_one({"_id": task_id}), BackgroundScheduleState
        )

    async def get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]:
        schedules = await self._ready("schedules")
        due: list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]] = []
        documents = schedules.find(
            {"paused": False, "next_due_at": {"$ne": None, "$lte": _naive(now)}}
        ).sort("next_due_at", 1)
        async for document in documents:
            if len(due) >= limit:
                break
            state = self._model(document, BackgroundScheduleState)
            task = await self.get_task(state.task_id)
            if task is None or not task.enabled:
                continue
            if task.schedule.type == ScheduleType.MANUAL:
                continue
            due.append(
                (
                    task,
                    state,
                    build_occurrence_id(
                        task.schedule.type, state.schedule_revision, state.next_due_at
                    ),
                )
            )
        return due

    async def advance_schedule(
        self,
        task_id: str,
        expected_revision: int,
        occurrence_id: str,
        next_due_at: datetime | None,
    ) -> BackgroundScheduleState:
        def change(state: BackgroundScheduleState) -> BackgroundScheduleState:
            if state.schedule_revision != expected_revision:
                raise TaskStoreError("Schedule revision mismatch")
            return self._advanced(state, next_due_at)

        return await self._change_schedule(task_id, change)

    async def _change_schedule(
        self,
        task_id: str,
        change: Callable[[BackgroundScheduleState], BackgroundScheduleState],
    ) -> BackgroundScheduleState:
        """Read, decide, and write the document back if nothing else did."""
        schedules = await self._ready("schedules")
        for _ in range(_RETRIES):
            document = await schedules.find_one({"_id": task_id})
            if not document:
                raise TaskNotFoundError(f"Schedule state not found for task: {task_id}")
            state = self._model(document, BackgroundScheduleState)
            updated = change(state)
            result = await schedules.update_one(
                {"_id": task_id, "version": document.get("version")},
                {
                    "$set": self._document(
                        updated,
                        next_due_at=_naive(updated.next_due_at),
                        paused=bool(updated.paused),
                        schedule_revision=updated.schedule_revision,
                    ),
                    "$inc": {"version": 1},
                },
            )
            if result.modified_count == 1:
                return updated
        raise TaskStoreError(f"Schedule for task {task_id} is being changed elsewhere")

    @staticmethod
    def _advanced(
        state: BackgroundScheduleState, next_due_at: datetime | None
    ) -> BackgroundScheduleState:
        return state.model_copy(
            update={
                "last_due_at": state.next_due_at,
                "last_dispatched_at": _now(),
                "next_due_at": _aware(next_due_at),
                "updated_at": _now(),
            },
            deep=True,
        )

    # --- runs -------------------------------------------------------------

    def _run_document(self, run: BackgroundRun) -> dict[str, Any]:
        return self._document(
            run,
            task_id=run.task_id,
            agent_id=run.agent_id,
            status=run.status.value,
            occurrence_id=run.occurrence_id,
            queued_at=_naive(run.queued_at or run.triggered_at),
            triggered_at=_naive(run.triggered_at),
            lease_expires_at=_naive(run.lease_expires_at),
            cancel_requested_at=_naive(run.cancel_requested_at),
            unfinished=run.status not in TERMINAL_RUN_STATUSES,
        )

    async def _run(self, run_id: str) -> BackgroundRun | None:
        runs = await self._ready("runs")
        return self._model(await runs.find_one({"_id": run_id}), BackgroundRun)

    async def _open_runs(self, task_id: str) -> list[BackgroundRun]:
        """A task's unfinished runs: bounded by work in flight, not history."""
        runs = await self._ready("runs")
        found = [
            self._model(document, BackgroundRun)
            async for document in runs.find({"task_id": task_id, "unfinished": True})
        ]
        return [run for run in found if run is not None]

    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun:
        if not run.occurrence_id:
            raise TaskStoreError("Scheduled runs require occurrence_id")
        runs = await self._ready("runs")
        taken = await runs.find_one(
            {"task_id": run.task_id, "occurrence_id": run.occurrence_id}
        )
        if taken:
            return self._model(taken, BackgroundRun)
        state = await self.get_schedule_state(run.task_id)
        if state is None:
            raise TaskNotFoundError(f"Schedule state not found for task: {run.task_id}")
        if state.schedule_revision != expected_schedule_revision:
            raise TaskStoreError("Schedule revision mismatch")
        created = await self.create_run_with_overlap_guard(run, overlap_policy)
        await self._change_schedule(
            run.task_id, lambda current: self._advanced(current, next_due_at)
        )
        return created

    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        runs = await self._ready("runs")
        if await self.get_task(run.task_id) is None:
            raise TaskNotFoundError(f"Task not found: {run.task_id}")
        if await runs.find_one({"_id": run.run_id}):
            raise TaskStoreError(f"Run already exists: {run.run_id}")
        active: list[BackgroundRun] = []
        if overlap_policy in {
            OverlapPolicy.SKIP_IF_RUNNING,
            OverlapPolicy.CANCEL_PREVIOUS,
        }:
            # Only these consult what else is open, so only they read it.
            active = [
                item
                for item in await self._open_runs(run.task_id)
                if item.status in ACTIVE_RUN_STATUSES
            ]
        if active and overlap_policy == OverlapPolicy.SKIP_IF_RUNNING:
            skipped = run.model_copy(
                update={"status": RunStatus.SKIPPED, "finished_at": _now()}, deep=True
            )
            await self._put("runs", skipped.run_id, self._run_document(skipped))
            return skipped
        if active and overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            for item in active:
                await runs.update_one(
                    {"_id": item.run_id},
                    {
                        "$set": self._run_document(
                            item.model_copy(
                                update={"cancel_requested_at": _now()}, deep=True
                            )
                        ),
                        "$inc": {"version": 1},
                    },
                )
        await self._put("runs", run.run_id, self._run_document(run))
        return run

    async def get_run(self, run_id: str) -> BackgroundRun | None:
        return await self._run(run_id)

    async def update_run_metadata(
        self,
        run_id: str,
        patch: dict,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        def change(run: BackgroundRun) -> BackgroundRun:
            if worker_id is not None or lease_token is not None:
                self._verify_lease(run, worker_id, lease_token)
            return run.model_copy(
                update={"metadata": {**run.metadata, **patch}}, deep=True
            )

        return await self._change_run(run_id, change)

    async def transition_run(
        self,
        run_id: str,
        expected: set[RunStatus],
        next_status: RunStatus,
        patch: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> BackgroundRun:
        def change(run: BackgroundRun) -> BackgroundRun:
            if run.status not in expected:
                raise TaskStoreError(
                    f"Run {run_id} status {run.status.value} not in expected states"
                )
            if next_status not in _ALLOWED_TRANSITIONS[run.status]:
                raise TaskStoreError(
                    f"Invalid run transition {run.status.value} -> {next_status.value}"
                )
            if run.status in {RunStatus.CLAIMED, RunStatus.RUNNING, RunStatus.RETRYING}:
                self._verify_lease(run, worker_id, lease_token)
            if (
                next_status
                in {RunStatus.COMPLETED, RunStatus.RETRYING, RunStatus.QUEUED}
                and run.cancel_requested_at is not None
            ):
                raise RunCancellationRequestedError(
                    "Run cancellation requested before non-terminal transition"
                )
            update = {**(patch or {}), "status": next_status}
            if next_status in TERMINAL_RUN_STATUSES:
                update.setdefault("finished_at", _now())
                update.setdefault("lease_owner", None)
                update.setdefault("lease_token", None)
                update.setdefault("lease_expires_at", None)
            return run.model_copy(update=update, deep=True)

        return await self._change_run(run_id, change)

    async def _change_run(
        self, run_id: str, change: Callable[[BackgroundRun], BackgroundRun]
    ) -> BackgroundRun:
        """Read one run, decide, and write it back if nothing else did."""
        runs = await self._ready("runs")
        for _ in range(_RETRIES):
            document = await runs.find_one({"_id": run_id})
            if not document:
                raise RunNotFoundError(f"Run not found: {run_id}")
            updated = change(self._model(document, BackgroundRun))
            result = await runs.update_one(
                {"_id": run_id, "version": document.get("version")},
                {"$set": self._run_document(updated), "$inc": {"version": 1}},
            )
            if result.modified_count == 1:
                return updated
        raise TaskStoreError(f"Run {run_id} is being changed elsewhere")

    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]:
        runs = await self._ready("runs")
        query: dict[str, Any] = {}
        if task_id is not None:
            query["task_id"] = task_id
        if status is not None:
            query["status"] = status.value
        found = [
            self._model(document, BackgroundRun)
            async for document in runs.find(query).sort("queued_at", 1)
        ]
        return self._sorted([run for run in found if run is not None], "queued_at")

    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]:
        runs = await self._ready("runs")
        query: dict[str, Any] = {
            "status": {"$in": [status.value for status in ACTIVE_RUN_STATUSES]}
        }
        if task_id is not None:
            query["task_id"] = task_id
        found = [
            self._model(document, BackgroundRun)
            async for document in runs.find(query).sort("queued_at", 1)
        ]
        return self._sorted([run for run in found if run is not None], "queued_at")

    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        return await self._claimable(limit)

    async def _claimable(self, limit: int) -> list[BackgroundRun]:
        """Queued runs whose turn it is, in order."""
        runs = await self._ready("runs")
        documents = (
            runs.find(
                {"status": RunStatus.QUEUED.value, "queued_at": {"$lte": _naive(_now())}}
            )
            .sort([("queued_at", 1), ("_id", 1)])
            .limit(max(limit, 1) + _CLAIM_CANDIDATES)
        )
        claimable: list[BackgroundRun] = []
        policies: dict[str, OverlapPolicy] = {}
        async for document in documents:
            if len(claimable) >= limit:
                break
            run = self._model(document, BackgroundRun)
            if run is None:
                continue
            if run.task_id not in policies:
                task = await self.get_task(run.task_id)
                if task is None:
                    continue
                policies[run.task_id] = task.overlap_policy
            if policies[run.task_id] == OverlapPolicy.QUEUE_NEXT:
                order = _run_order_key(run)
                earlier = [
                    item
                    for item in await self._open_runs(run.task_id)
                    if item.run_id != run.run_id
                    and item.status in _UNFINISHED
                    and _run_order_key(item) < order
                ]
                if earlier:
                    continue
            claimable.append(run)
        return claimable

    async def claim_next_run(
        self, worker_id: str, lease_seconds: int
    ) -> BackgroundRun | None:
        for run in await self._claimable(_CLAIM_CANDIDATES):
            try:
                return await self._claim(run.run_id, worker_id, lease_seconds)
            except (RunLeaseError, TaskStoreError):
                continue  # another worker took it; try the next one
        return None

    async def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        run = await self._run(run_id)
        if run is None:
            raise RunNotFoundError(f"Run not found: {run_id}")
        if run.status != RunStatus.QUEUED:
            raise RunLeaseError(f"Run {run_id} is not queued")
        if run_id not in {item.run_id for item in await self._claimable(_CLAIM_CANDIDATES)}:
            raise RunLeaseError(f"Run {run_id} is blocked by overlap policy")
        return await self._claim(run_id, worker_id, lease_seconds)

    async def _claim(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        def change(run: BackgroundRun) -> BackgroundRun:
            if run.status != RunStatus.QUEUED:
                raise RunLeaseError(f"Run {run_id} is not queued")
            return self._leased(run, worker_id, lease_seconds, claim=True)

        return await self._change_run(run_id, change)

    async def steal_expired_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> BackgroundRun:
        def change(run: BackgroundRun) -> BackgroundRun:
            if not run.lease_expires_at or _aware(run.lease_expires_at) > _now():
                raise RunLeaseError(f"Run {run_id} lease has not expired")
            return self._leased(run, worker_id, lease_seconds, claim=False)

        return await self._change_run(run_id, change)

    @staticmethod
    def _leased(
        run: BackgroundRun, worker_id: str, lease_seconds: int, *, claim: bool
    ) -> BackgroundRun:
        update = {
            "lease_owner": worker_id,
            "lease_token": uuid4().hex,
            "lease_generation": run.lease_generation + 1,
            "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
            "heartbeat_at": _now(),
        }
        if claim:
            update["status"] = RunStatus.CLAIMED
            update["claimed_at"] = _now()
        return run.model_copy(update=update, deep=True)

    async def refresh_lease(
        self, run_id: str, worker_id: str, lease_token: str, lease_seconds: int
    ) -> None:
        def change(run: BackgroundRun) -> BackgroundRun:
            self._verify_lease(run, worker_id, lease_token)
            return run.model_copy(
                update={
                    "heartbeat_at": _now(),
                    "lease_expires_at": _now() + timedelta(seconds=lease_seconds),
                },
                deep=True,
            )

        await self._change_run(run_id, change)

    async def release_lease(self, run_id: str, worker_id: str, lease_token: str) -> None:
        def change(run: BackgroundRun) -> BackgroundRun:
            self._verify_lease(run, worker_id, lease_token)
            return run.model_copy(
                update={
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
                deep=True,
            )

        await self._change_run(run_id, change)

    async def list_expired_leases(self, now: datetime) -> list[BackgroundRun]:
        runs = await self._ready("runs")
        moment = _aware(now)
        documents = runs.find(
            {
                "status": {"$in": [status.value for status in _LEASED]},
                "lease_expires_at": {"$ne": None, "$lte": _naive(now)},
            }
        ).sort("lease_expires_at", 1)
        found = []
        async for document in documents:
            run = self._model(document, BackgroundRun)
            if (
                run is not None
                and run.lease_expires_at is not None
                and _aware(run.lease_expires_at) <= moment
            ):
                found.append(run)
        return self._sorted(found, "lease_expires_at")

    async def request_cancel(self, run_id: str) -> None:
        def change(run: BackgroundRun) -> BackgroundRun:
            return run.model_copy(update={"cancel_requested_at": _now()}, deep=True)

        await self._change_run(run_id, change)

    async def is_cancel_requested(self, run_id: str) -> bool:
        runs = await self._ready("runs")
        document = await runs.find_one(
            {"_id": run_id}, projection={"cancel_requested_at": 1}
        )
        return bool(document and document.get("cancel_requested_at") is not None)

    # --- attempts ---------------------------------------------------------

    async def create_attempt(self, attempt: BackgroundAttempt) -> None:
        run = await self._run(attempt.run_id)
        if run is None:
            raise RunNotFoundError(f"Run not found: {attempt.run_id}")
        self._verify_lease(run, attempt.worker_id, attempt.lease_token)
        attempts = await self._ready("attempts")
        if await attempts.find_one({"_id": attempt.attempt_id}):
            raise TaskStoreError(f"Attempt already exists: {attempt.attempt_id}")
        await self._put(
            "attempts",
            attempt.attempt_id,
            self._document(
                attempt,
                run_id=attempt.run_id,
                attempt_number=attempt.attempt_number,
            ),
        )

    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        attempts = await self._ready("attempts")
        document = await attempts.find_one({"_id": attempt_id})
        if not document:
            raise TaskStoreError(f"Attempt not found: {attempt_id}")
        attempt = self._model(document, BackgroundAttempt)
        run = await self._run(attempt.run_id)
        if run is None:
            raise RunNotFoundError(f"Run not found: {attempt.run_id}")
        self._verify_lease(run, worker_id, lease_token)
        updated = attempt.model_copy(update=patch, deep=True)
        await self._put(
            "attempts",
            attempt_id,
            self._document(
                updated, run_id=updated.run_id, attempt_number=updated.attempt_number
            ),
        )
        return updated

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        attempts = await self._ready("attempts")
        found = [
            self._model(document, BackgroundAttempt)
            async for document in attempts.find({"run_id": run_id}).sort(
                "attempt_number", 1
            )
        ]
        return [attempt for attempt in found if attempt is not None]

    # --- leases and ordering ----------------------------------------------

    @staticmethod
    def _verify_lease(
        run: BackgroundRun, worker_id: str | None, lease_token: str | None
    ) -> None:
        if not worker_id or not lease_token:
            raise RunLeaseError("worker_id and lease_token are required")
        if run.lease_owner != worker_id or run.lease_token != lease_token:
            raise RunLeaseError("Run lease token mismatch")
        if run.lease_expires_at is not None and _aware(run.lease_expires_at) <= _now():
            raise RunLeaseError("Run lease has expired")

    @staticmethod
    def _sorted(items: list[Any], field: str = "created_at") -> list[Any]:
        return sorted(items, key=lambda item: getattr(item, field, None) or _now())

    # --- the snapshot store's state ---------------------------------------

    async def _import_snapshot_state(self) -> None:
        """Take over what the snapshot store wrote, once.

        It kept a generation of records and a pointer at the live one. If that
        pointer is there and nothing has been written a document at a time yet,
        its records become entities; the old collections are left alone, so a
        downgrade still finds its own state.
        """
        locks = self._collection("locks")
        state = await locks.find_one({"_id": "task_store"})
        generation = (state or {}).get("active_generation")
        if not generation:
            return
        if await self._collection("runs").find_one({}) or await self._collection(
            "tasks"
        ).find_one({}):
            return
        documents = self._collection("snapshots").find({"_generation": generation})
        kept: dict[str, dict[str, Any]] = {
            "agents": {},
            "tasks": {},
            "schedule_states": {},
            "runs": {},
            "attempts": {},
        }
        cancelled: set[str] = set()
        async for document in documents:
            category = document.get("category")
            if category == "cancel_requested":
                cancelled.add(document["record_id"])
            elif category in kept:
                kept[category][document["record_id"]] = document["value"]

        for record in kept["agents"].values():
            await self.save_agent(BackgroundAgentSpec.model_validate(record))
        for record in kept["tasks"].values():
            await self.save_task(BackgroundTaskSpec.model_validate(record))
        for task_id, record in kept["schedule_states"].items():
            if task_id in kept["tasks"]:
                await self._write_schedule(
                    BackgroundScheduleState.model_validate(record)
                )
        for run_id, record in kept["runs"].items():
            run = BackgroundRun.model_validate(record)
            if run_id in cancelled and run.cancel_requested_at is None:
                run = run.model_copy(update={"cancel_requested_at": _now()}, deep=True)
            await self._put("runs", run.run_id, self._run_document(run))
        for record in kept["attempts"].values():
            attempt = BackgroundAttempt.model_validate(record)
            await self._put(
                "attempts",
                attempt.attempt_id,
                self._document(
                    attempt,
                    run_id=attempt.run_id,
                    attempt_number=attempt.attempt_number,
                ),
            )
