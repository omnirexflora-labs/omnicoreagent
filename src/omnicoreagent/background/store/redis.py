"""A durable task store in Redis: an entity per key.

Scale plan, S2b. What this replaced was a snapshot store, and the most
expensive kind: every mutation took a lock over the whole store, read all of
its state, wrote a complete new generation of every hash, and flipped a
pointer at it. So one write copied everything the store had ever kept, and two
copies of it lived at once — 6.6 ms for a run write with a hundred runs kept,
103.5 ms with two thousand, and nothing prunes run history.

Redis is a key-value store, so an entity gets a key:

    {prefix}:agent:{id} :task:{id} :schedule:{task} :run:{id} :attempt:{id}
        one hash each, holding ``data`` (the entity as JSON) and ``version``
    {prefix}:agents :tasks                  the ids, for listing
    {prefix}:runs :task_runs:{task}         the run ids, for listing
    {prefix}:queued                         sorted set, the claim order
    {prefix}:open:{task}                    sorted set, a task's unfinished runs
    {prefix}:leased                         sorted set by lease expiry
    {prefix}:occurrence:{task}:{id}         the run a schedule occurrence made
    {prefix}:run_attempts:{run}             sorted set by attempt number

A write touches the keys it writes. Mutations are optimistic transactions: the
keys are watched, read, and written in one atomic step, and retried if anything
else changed them meanwhile. Two workers therefore contend only where they
overlap, and a claim writes the run it read under watch, so exactly one worker
wins.

State the snapshot store wrote is imported on first use, so a deployment that
upgrades keeps its agents, tasks, runs and attempts; the old keys are left
where they are.
"""

from __future__ import annotations

import json
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
# poll, and how many times an optimistic transaction is retried.
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


def _score(value: datetime | None) -> float:
    """A sorted set's score: when, in seconds. For ordering only."""
    moment = _aware(value)
    return moment.timestamp() if moment is not None else 0.0


def _run_order_key(run: BackgroundRun) -> tuple[datetime, datetime, str]:
    queued_at = run.queued_at or run.triggered_at
    return (queued_at, run.triggered_at, run.run_id)


class RedisTaskStore(AbstractTaskStore):
    """Durable Redis task store: an entity per key, written a key at a time."""

    def __init__(
        self,
        url: str,
        *,
        prefix: str | None = None,
        connect_timeout: float | None = None,
        lock_timeout: float = 30.0,
        lock_lease_seconds: float = 30.0,
    ) -> None:
        self.url = url
        self.prefix = (prefix or "omnicoreagent:background").rstrip(":")
        self.connect_timeout = connect_timeout
        # Taken for callers that configured the snapshot store's lock: nothing
        # holds a lock over the whole store any more.
        self.lock_timeout = lock_timeout
        self.lock_lease_seconds = lock_lease_seconds
        self._client: Any = None
        self._closed = False

    # --- plumbing ---------------------------------------------------------

    def key(self, *parts: str) -> str:
        return ":".join((self.prefix, *parts))

    def _require_client(self) -> Any:
        if self._client is None:
            raise TaskStoreError("Redis task store is not initialized")
        return self._client

    async def _connection(self) -> Any:
        """The client, connecting on first use.

        A manager is given its store before it is initialized and uses it
        straight away, so an operation connects rather than refusing.
        """
        if self._client is None:
            await self.initialize()
        return self._require_client()

    async def initialize(self) -> None:
        self._closed = False
        if self._client is not None:
            return
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise InvalidTaskStoreError(
                "Redis task store requires the redis extra: "
                "pip install 'omnicoreagent[redis]'"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.connect_timeout is not None:
            kwargs["socket_connect_timeout"] = self.connect_timeout
        self._client = Redis.from_url(self.url, decode_responses=True, **kwargs)
        await self._client.ping()
        await self._import_snapshot_state()

    async def close(self) -> None:
        self._closed = True
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def _transaction(self, keys: list[str], work: Callable[[Any], Any]) -> Any:
        """Watch these keys, read them, then write in one atomic step.

        ``work`` reads through the pipeline, calls ``pipe.multi()``, queues its
        writes and returns what the caller should get back. If another client
        wrote a watched key meanwhile, the whole thing is read again.
        """
        from redis.exceptions import WatchError

        client = await self._connection()
        for _ in range(_RETRIES):
            async with client.pipeline() as pipe:
                try:
                    if keys:
                        await pipe.watch(*keys)
                    result = await work(pipe)
                    await pipe.execute()
                    return result
                except WatchError:
                    continue  # a watched key changed; decide again on what is there
        raise TaskStoreError("Redis task store: too much contention on one key")

    def _write(self, pipe, key: str, model: Any) -> None:
        """One entity, and its version raised without having to read it."""
        pipe.hset(key, "data", model.model_dump_json())
        pipe.hincrby(key, "version", 1)

    async def _load(self, key: str, model: Any) -> Any | None:
        client = await self._connection()
        data = await client.hget(key, "data")
        return model.model_validate_json(data) if data else None

    # --- agents -----------------------------------------------------------

    async def save_agent(self, spec: BackgroundAgentSpec) -> None:
        key = self.key("agent", spec.agent_id)

        async def work(pipe):
            pipe.multi()
            self._write(pipe, key, spec)
            pipe.sadd(self.key("agents"), spec.agent_id)

        await self._transaction([key], work)

    async def get_agent(self, agent_id: str) -> BackgroundAgentSpec | None:
        return await self._load(self.key("agent", agent_id), BackgroundAgentSpec)

    async def delete_agent(self, agent_id: str) -> None:
        client = await self._connection()
        async with client.pipeline(transaction=True) as pipe:
            pipe.delete(self.key("agent", agent_id))
            pipe.srem(self.key("agents"), agent_id)
            await pipe.execute()

    async def list_agents(self) -> list[BackgroundAgentSpec]:
        client = await self._connection()
        agents = []
        for agent_id in sorted(await client.smembers(self.key("agents"))):
            found = await self._load(self.key("agent", agent_id), BackgroundAgentSpec)
            if found is not None:
                agents.append(found)
        return self._sorted(agents)

    # --- tasks ------------------------------------------------------------

    async def save_task(self, spec: BackgroundTaskSpec) -> None:
        if await self.get_agent(spec.agent_id) is None:
            raise AgentNotFoundError(f"Agent not found: {spec.agent_id}")
        task_key = self.key("task", spec.task_id)
        schedule_key = self.key("schedule", spec.task_id)

        async def work(pipe):
            existing = await pipe.hget(task_key, "data")
            current = await pipe.hget(schedule_key, "data")
            before = (
                BackgroundTaskSpec.model_validate_json(existing) if existing else None
            )
            state = None
            if before is None or before.schedule != spec.schedule:
                revision = 1
                if before is not None and current:
                    revision = (
                        BackgroundScheduleState.model_validate_json(
                            current
                        ).schedule_revision
                        + 1
                    )
                state = BackgroundScheduleState(
                    task_id=spec.task_id,
                    next_due_at=initial_schedule_due(spec.schedule, _now()),
                    schedule_revision=revision,
                )
            pipe.multi()
            self._write(pipe, task_key, spec)
            pipe.sadd(self.key("tasks"), spec.task_id)
            if state is not None:
                self._write(pipe, schedule_key, state)

        await self._transaction([task_key, schedule_key], work)

    async def get_task(self, task_id: str) -> BackgroundTaskSpec | None:
        return await self._load(self.key("task", task_id), BackgroundTaskSpec)

    async def delete_task(self, task_id: str) -> None:
        client = await self._connection()
        async with client.pipeline(transaction=True) as pipe:
            pipe.delete(self.key("task", task_id))
            pipe.delete(self.key("schedule", task_id))
            pipe.srem(self.key("tasks"), task_id)
            await pipe.execute()

    async def delete_runs_for_task(self, task_id: str) -> None:
        client = await self._connection()
        for run_id in await client.smembers(self.key("task_runs", task_id)):
            attempts = await client.zrange(self.key("run_attempts", run_id), 0, -1)
            async with client.pipeline(transaction=True) as pipe:
                for attempt_id in attempts:
                    pipe.delete(self.key("attempt", attempt_id))
                pipe.delete(self.key("run_attempts", run_id))
                pipe.delete(self.key("run", run_id))
                pipe.srem(self.key("runs"), run_id)
                pipe.zrem(self.key("queued"), run_id)
                pipe.zrem(self.key("leased"), run_id)
                await pipe.execute()
        async with client.pipeline(transaction=True) as pipe:
            pipe.delete(self.key("task_runs", task_id))
            pipe.delete(self.key("open", task_id))
            await pipe.execute()

    async def list_tasks(
        self, agent_id: str | None = None, enabled: bool | None = None
    ) -> list[BackgroundTaskSpec]:
        client = await self._connection()
        tasks = []
        for task_id in sorted(await client.smembers(self.key("tasks"))):
            task = await self._load(self.key("task", task_id), BackgroundTaskSpec)
            if task is None:
                continue
            if agent_id is not None and task.agent_id != agent_id:
                continue
            if enabled is not None and task.enabled is not bool(enabled):
                continue
            tasks.append(task)
        return self._sorted(tasks)

    # --- schedules --------------------------------------------------------

    async def save_schedule_state(self, state: BackgroundScheduleState) -> None:
        if await self.get_task(state.task_id) is None:
            raise TaskNotFoundError(f"Task not found: {state.task_id}")
        key = self.key("schedule", state.task_id)

        async def work(pipe):
            pipe.multi()
            self._write(pipe, key, state)

        await self._transaction([key], work)

    async def set_schedule_paused(
        self, task_id: str, paused: bool, *, reason: str | None = None
    ) -> BackgroundScheduleState:
        key = self.key("schedule", task_id)

        async def work(pipe):
            data = await pipe.hget(key, "data")
            if not data:
                raise TaskNotFoundError(f"Schedule state not found for task: {task_id}")
            state = BackgroundScheduleState.model_validate_json(data)
            updated = state.model_copy(
                update={
                    "paused": paused,
                    "paused_reason": reason if paused else None,
                    "updated_at": _now(),
                },
                deep=True,
            )
            pipe.multi()
            self._write(pipe, key, updated)
            return updated

        return await self._transaction([key], work)

    async def get_schedule_state(self, task_id: str) -> BackgroundScheduleState | None:
        return await self._load(self.key("schedule", task_id), BackgroundScheduleState)

    async def get_due_schedules(
        self, now: datetime, limit: int
    ) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]:
        moment = _aware(now)
        due: list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]] = []
        for task in await self.list_tasks():
            if len(due) >= limit:
                break
            if not task.enabled or task.schedule.type == ScheduleType.MANUAL:
                continue
            state = await self.get_schedule_state(task.task_id)
            if state is None or state.paused:
                continue
            if state.next_due_at is None or _aware(state.next_due_at) > moment:
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
        key = self.key("schedule", task_id)

        async def work(pipe):
            data = await pipe.hget(key, "data")
            if not data:
                raise TaskNotFoundError(f"Schedule state not found for task: {task_id}")
            state = BackgroundScheduleState.model_validate_json(data)
            if state.schedule_revision != expected_revision:
                raise TaskStoreError("Schedule revision mismatch")
            advanced = self._advanced(state, next_due_at)
            pipe.multi()
            self._write(pipe, key, advanced)
            return advanced

        return await self._transaction([key], work)

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

    def _run_writes(self, pipe, run: BackgroundRun) -> None:
        """The run's key, and every index it belongs in."""
        self._write(pipe, self.key("run", run.run_id), run)
        pipe.sadd(self.key("runs"), run.run_id)
        pipe.sadd(self.key("task_runs", run.task_id), run.run_id)
        order = _score(run.queued_at or run.triggered_at)
        if run.status == RunStatus.QUEUED:
            pipe.zadd(self.key("queued"), {run.run_id: order})
        else:
            pipe.zrem(self.key("queued"), run.run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            pipe.zrem(self.key("open", run.task_id), run.run_id)
        else:
            pipe.zadd(self.key("open", run.task_id), {run.run_id: order})
        if run.status in _LEASED and run.lease_expires_at is not None:
            pipe.zadd(self.key("leased"), {run.run_id: _score(run.lease_expires_at)})
        else:
            pipe.zrem(self.key("leased"), run.run_id)
        if run.occurrence_id:
            pipe.set(self.key("occurrence", run.task_id, run.occurrence_id), run.run_id)

    async def _run(self, run_id: str) -> BackgroundRun | None:
        return await self._load(self.key("run", run_id), BackgroundRun)

    async def _open_runs(self, task_id: str) -> list[BackgroundRun]:
        """A task's unfinished runs: bounded by work in flight, not history."""
        client = await self._connection()
        runs = []
        for run_id in await client.zrange(self.key("open", task_id), 0, -1):
            run = await self._run(run_id)
            if run is not None:
                runs.append(run)
        return runs

    async def dispatch_scheduled_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        expected_schedule_revision: int,
        next_due_at: datetime | None,
    ) -> BackgroundRun:
        if not run.occurrence_id:
            raise TaskStoreError("Scheduled runs require occurrence_id")
        task_exists = await self.get_task(run.task_id) is not None
        schedule_key = self.key("schedule", run.task_id)
        occurrence_key = self.key("occurrence", run.task_id, run.occurrence_id)
        open_key = self.key("open", run.task_id)

        async def work(pipe):
            data = await pipe.hget(schedule_key, "data")
            if not data:
                raise TaskNotFoundError(
                    f"Schedule state not found for task: {run.task_id}"
                )
            state = BackgroundScheduleState.model_validate_json(data)
            if state.schedule_revision != expected_schedule_revision:
                raise TaskStoreError("Schedule revision mismatch")
            taken = await pipe.get(occurrence_key)
            if taken:
                existing = await pipe.hget(self.key("run", taken), "data")
                if existing:
                    pipe.multi()
                    return BackgroundRun.model_validate_json(existing)
            open_runs = await self._open_for(pipe, overlap_policy, open_key)
            plan = self._plan_run(run, overlap_policy, open_runs, task_exists)
            pipe.multi()
            self._apply_plan(pipe, plan)
            self._write(pipe, schedule_key, self._advanced(state, next_due_at))
            return plan["run"]

        return await self._transaction([schedule_key, occurrence_key, open_key], work)

    async def _open_for(
        self, pipe, overlap_policy: OverlapPolicy, open_key: str
    ) -> list[BackgroundRun]:
        """A task's open runs, read only by the policies that consult them.

        ``allow_parallel`` and ``queue_next`` do not care what else is open when
        a run is created, so creating one under them touches only the run being
        written.
        """
        if overlap_policy not in {
            OverlapPolicy.SKIP_IF_RUNNING,
            OverlapPolicy.CANCEL_PREVIOUS,
        }:
            return []
        return await self._read_runs(pipe, open_key)

    async def _read_runs(self, pipe, zset_key: str) -> list[BackgroundRun]:
        """The runs in a sorted set, read inside a transaction's watch."""
        runs = []
        for run_id in await pipe.zrange(zset_key, 0, -1):
            data = await pipe.hget(self.key("run", run_id), "data")
            if data:
                runs.append(BackgroundRun.model_validate_json(data))
        return runs

    async def create_run_with_overlap_guard(
        self, run: BackgroundRun, overlap_policy: OverlapPolicy
    ) -> BackgroundRun:
        task_exists = await self.get_task(run.task_id) is not None
        run_key = self.key("run", run.run_id)
        open_key = self.key("open", run.task_id)

        async def work(pipe):
            if await pipe.exists(run_key):
                raise TaskStoreError(f"Run already exists: {run.run_id}")
            open_runs = await self._open_for(pipe, overlap_policy, open_key)
            plan = self._plan_run(run, overlap_policy, open_runs, task_exists)
            pipe.multi()
            self._apply_plan(pipe, plan)
            return plan["run"]

        return await self._transaction([run_key, open_key], work)

    def _plan_run(
        self,
        run: BackgroundRun,
        overlap_policy: OverlapPolicy,
        open_runs: list[BackgroundRun],
        task_exists: bool,
    ) -> dict[str, Any]:
        """What creating this run means, decided before anything is written."""
        if not task_exists:
            raise TaskNotFoundError(f"Task not found: {run.task_id}")
        active = [item for item in open_runs if item.status in ACTIVE_RUN_STATUSES]
        if active and overlap_policy == OverlapPolicy.SKIP_IF_RUNNING:
            return {
                "run": run.model_copy(
                    update={"status": RunStatus.SKIPPED, "finished_at": _now()},
                    deep=True,
                ),
                "cancel": [],
            }
        cancel: list[BackgroundRun] = []
        if active and overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            cancel = [
                item.model_copy(update={"cancel_requested_at": _now()}, deep=True)
                for item in active
            ]
        return {"run": run, "cancel": cancel}

    def _apply_plan(self, pipe, plan: dict[str, Any]) -> None:
        for cancelled in plan["cancel"]:
            self._run_writes(pipe, cancelled)
        self._run_writes(pipe, plan["run"])

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
        key = self.key("run", run_id)

        async def work(pipe):
            data = await pipe.hget(key, "data")
            if not data:
                raise RunNotFoundError(f"Run not found: {run_id}")
            updated = change(BackgroundRun.model_validate_json(data))
            pipe.multi()
            self._run_writes(pipe, updated)
            return updated

        return await self._transaction([key], work)

    async def list_runs(
        self, task_id: str | None = None, status: RunStatus | None = None
    ) -> list[BackgroundRun]:
        client = await self._connection()
        key = self.key("task_runs", task_id) if task_id else self.key("runs")
        runs = []
        for run_id in sorted(await client.smembers(key)):
            run = await self._run(run_id)
            if run is None:
                continue
            if status is not None and run.status != status:
                continue
            runs.append(run)
        return self._sorted(runs, "queued_at")

    async def list_active_runs(self, task_id: str | None = None) -> list[BackgroundRun]:
        runs = [
            run
            for run in await self.list_runs(task_id=task_id)
            if run.status in ACTIVE_RUN_STATUSES
        ]
        return self._sorted(runs, "queued_at")

    async def list_claimable_runs(self, limit: int) -> list[BackgroundRun]:
        return await self._claimable(limit)

    async def _claimable(self, limit: int) -> list[BackgroundRun]:
        """Queued runs whose turn it is, in order."""
        client = await self._connection()
        candidates = await client.zrangebyscore(
            self.key("queued"),
            "-inf",
            _score(_now()),
            start=0,
            num=max(limit, 1) + _CLAIM_CANDIDATES,
        )
        claimable: list[BackgroundRun] = []
        policies: dict[str, OverlapPolicy] = {}
        for run_id in candidates:
            if len(claimable) >= limit:
                break
            run = await self._run(run_id)
            if run is None or run.status != RunStatus.QUEUED:
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
        client = await self._connection()
        moment = _aware(now)
        runs = []
        for run_id in await client.zrangebyscore(self.key("leased"), "-inf", _score(now)):
            run = await self._run(run_id)
            if (
                run is not None
                and run.status in _LEASED
                and run.lease_expires_at is not None
                and _aware(run.lease_expires_at) <= moment
            ):
                runs.append(run)
        return self._sorted(runs, "lease_expires_at")

    async def request_cancel(self, run_id: str) -> None:
        def change(run: BackgroundRun) -> BackgroundRun:
            return run.model_copy(update={"cancel_requested_at": _now()}, deep=True)

        await self._change_run(run_id, change)

    async def is_cancel_requested(self, run_id: str) -> bool:
        run = await self._run(run_id)
        return bool(run is not None and run.cancel_requested_at is not None)

    # --- attempts ---------------------------------------------------------

    async def create_attempt(self, attempt: BackgroundAttempt) -> None:
        run = await self._run(attempt.run_id)
        if run is None:
            raise RunNotFoundError(f"Run not found: {attempt.run_id}")
        self._verify_lease(run, attempt.worker_id, attempt.lease_token)
        key = self.key("attempt", attempt.attempt_id)

        async def work(pipe):
            if await pipe.exists(key):
                raise TaskStoreError(f"Attempt already exists: {attempt.attempt_id}")
            pipe.multi()
            self._write(pipe, key, attempt)
            pipe.zadd(
                self.key("run_attempts", attempt.run_id),
                {attempt.attempt_id: attempt.attempt_number},
            )

        await self._transaction([key], work)

    async def update_attempt(
        self, attempt_id: str, patch: dict, worker_id: str, lease_token: str
    ) -> BackgroundAttempt:
        key = self.key("attempt", attempt_id)

        async def work(pipe):
            data = await pipe.hget(key, "data")
            if not data:
                raise TaskStoreError(f"Attempt not found: {attempt_id}")
            attempt = BackgroundAttempt.model_validate_json(data)
            run_data = await pipe.hget(self.key("run", attempt.run_id), "data")
            if not run_data:
                raise RunNotFoundError(f"Run not found: {attempt.run_id}")
            self._verify_lease(
                BackgroundRun.model_validate_json(run_data), worker_id, lease_token
            )
            updated = attempt.model_copy(update=patch, deep=True)
            pipe.multi()
            self._write(pipe, key, updated)
            return updated

        return await self._transaction([key], work)

    async def list_attempts(self, run_id: str) -> list[BackgroundAttempt]:
        client = await self._connection()
        attempts = []
        for attempt_id in await client.zrange(self.key("run_attempts", run_id), 0, -1):
            found = await self._load(self.key("attempt", attempt_id), BackgroundAttempt)
            if found is not None:
                attempts.append(found)
        attempts.sort(key=lambda item: item.attempt_number)
        return attempts

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

        It kept a generation of hashes and a pointer at the live one. If that
        pointer is there and nothing has been written a key at a time yet, its
        records become entities; the old keys are left alone, so a downgrade
        still finds its own state.
        """
        client = self._client
        generation = await client.get(f"{self.prefix}:active_generation")
        if not generation:
            return
        if await client.exists(self.key("runs")) or await client.exists(
            self.key("tasks")
        ):
            return

        async def records_of(name: str) -> dict[str, Any]:
            raw = await client.hgetall(f"{self.prefix}:gen:{generation}:{name}")
            return {key: json.loads(value) for key, value in (raw or {}).items()}

        for record in (await records_of("agents")).values():
            await self.save_agent(BackgroundAgentSpec.model_validate(record))
        tasks = await records_of("tasks")
        for record in tasks.values():
            await self.save_task(BackgroundTaskSpec.model_validate(record))
        for task_id, record in (await records_of("schedule_states")).items():
            if task_id in tasks:
                await self.save_schedule_state(
                    BackgroundScheduleState.model_validate(record)
                )
        cancelled = set(
            await client.smembers(f"{self.prefix}:gen:{generation}:cancelled") or ()
        )
        for run_id, record in (await records_of("runs")).items():
            run = BackgroundRun.model_validate(record)
            if run_id in cancelled and run.cancel_requested_at is None:
                run = run.model_copy(update={"cancel_requested_at": _now()}, deep=True)

            async def keep(pipe, kept=run):
                pipe.multi()
                self._run_writes(pipe, kept)
                return kept

            await self._transaction([self.key("run", run.run_id)], keep)
        for record in (await records_of("attempts")).values():
            attempt = BackgroundAttempt.model_validate(record)
            async with client.pipeline(transaction=True) as pipe:
                self._write(pipe, self.key("attempt", attempt.attempt_id), attempt)
                pipe.zadd(
                    self.key("run_attempts", attempt.run_id),
                    {attempt.attempt_id: attempt.attempt_number},
                )
                await pipe.execute()
