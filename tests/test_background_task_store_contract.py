from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from omnicoreagent.background import (
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    InMemoryTaskStore,
    MongoDbTaskStore,
    OverlapPolicy,
    RedisTaskStore,
    RetryPolicy,
    RunCancellationRequestedError,
    RunLeaseError,
    RunStatus,
    SqlTaskStore,
    TaskStoreError,
)
from omnicoreagent.background.models import (
    AttemptStatus,
    ScheduleType,
    TriggerType,
    build_occurrence_id,
)


REDIS_CONTRACT_URL_ENV = "OMNICOREAGENT_TEST_REDIS_URL"
DEFAULT_REDIS_CONTRACT_URL = "redis://localhost:6379/0"
REDIS_CONTRACT_CONNECT_TIMEOUT = 2.0
MONGODB_CONTRACT_URI_ENV = "OMNICOREAGENT_TEST_MONGODB_URI"
MONGODB_CONTRACT_DATABASE_ENV = "OMNICOREAGENT_TEST_MONGODB_DATABASE"
DEFAULT_MONGODB_CONTRACT_URI = "mongodb://localhost:27017"
DEFAULT_MONGODB_CONTRACT_DATABASE = "omnicoreagent_test"
SHARED_CONTRACT_BACKENDS = ["in_memory", "sql", "redis", "mongodb"]
_UNAVAILABLE_BACKENDS: dict[str, str] = {}


def agent_spec(agent_id: str = "agent") -> BackgroundAgentSpec:
    return BackgroundAgentSpec(agent_id=agent_id)


def task_spec(
    task_id: str = "task",
    *,
    schedule: dict | None = None,
    overlap_policy: OverlapPolicy = OverlapPolicy.ALLOW_PARALLEL,
    retry_policy: RetryPolicy | None = None,
) -> BackgroundTaskSpec:
    return BackgroundTaskSpec(
        task_id=task_id,
        agent_id="agent",
        query=f"run {task_id}",
        schedule=schedule or {"type": "manual"},
        overlap_policy=overlap_policy,
        retry_policy=retry_policy or RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )


def background_run(
    task_id: str = "task",
    *,
    occurrence_id: str | None = None,
    queued_at: datetime | None = None,
    trigger_type: TriggerType = TriggerType.MANUAL,
    due_at: datetime | None = None,
) -> BackgroundRun:
    return BackgroundRun(
        task_id=task_id,
        agent_id="agent",
        query_snapshot=f"snapshot {task_id}",
        trigger_type=trigger_type,
        session_id=f"session-{task_id}",
        workspace_path=f"background/agent/{task_id}/run",
        occurrence_id=occurrence_id,
        due_at=due_at,
        queued_at=queued_at,
    )


async def create_store(kind: str, tmp_path):
    skip_if_backend_unavailable(kind)
    if kind == "in_memory":
        store = InMemoryTaskStore()
    elif kind == "sql":
        store = SqlTaskStore(url=f"sqlite:///{tmp_path / 'background.db'}")
    elif kind == "redis":
        store = RedisTaskStore(
            url=redis_contract_url(),
            prefix=f"test:omnicoreagent:background:{uuid4().hex}",
            connect_timeout=REDIS_CONTRACT_CONNECT_TIMEOUT,
            lock_timeout=0.5,
        )
    elif kind == "mongodb":
        store = MongoDbTaskStore(
            uri=mongodb_contract_uri(),
            database=mongodb_contract_database(),
            collection_prefix=f"test_omnicoreagent_background_{uuid4().hex}",
            connect_timeout=20,
            lock_timeout=5,
        )
    else:  # pragma: no cover
        raise AssertionError(f"unknown store kind: {kind}")
    try:
        await store.initialize()
    except Exception as exc:
        await store.close()
        if kind == "redis":
            skip_backend(kind, redis_unavailable_message(exc))
        if kind == "mongodb":
            skip_backend(kind, mongodb_unavailable_message(exc))
        raise
    return store


async def close_store(store) -> None:
    if isinstance(store, RedisTaskStore):
        try:
            await cleanup_redis_prefix(store)
        finally:
            await store.close()
        return
    if isinstance(store, MongoDbTaskStore):
        try:
            await cleanup_mongodb_prefix(store)
        finally:
            await store.close()
        return
    await store.close()


def skip_if_backend_unavailable(kind: str) -> None:
    if kind in _UNAVAILABLE_BACKENDS:
        pytest.skip(_UNAVAILABLE_BACKENDS[kind])


def skip_backend(kind: str, message: str) -> None:
    if kind == "redis" and os.getenv(REDIS_CONTRACT_URL_ENV):
        raise AssertionError(message)
    if kind == "mongodb" and os.getenv(MONGODB_CONTRACT_URI_ENV):
        raise AssertionError(message)
    _UNAVAILABLE_BACKENDS[kind] = message
    pytest.skip(message)


async def save_mutated_schedule_state(store) -> None:
    await store.save_task(task_spec(schedule={"type": "interval", "seconds": 60}))
    state = await store.get_schedule_state("task")
    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    state = state.model_copy(update={"next_due_at": due_at}, deep=True)
    await store.save_schedule_state(state)
    await store.advance_schedule(
        "task",
        expected_revision=state.schedule_revision,
        occurrence_id=build_occurrence_id(
            ScheduleType.INTERVAL, state.schedule_revision, due_at
        ),
        next_due_at=due_at + timedelta(seconds=60),
    )
    await store.set_schedule_paused("task", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_core_records_and_deterministic_lists(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await store.save_task(
            task_spec(schedule={"type": "once", "run_at": due_at})
        )

        assert (await store.get_agent("agent")).agent_id == "agent"
        assert (await store.get_task("task")).query == "run task"
        assert [task.task_id for task in await store.list_tasks()] == ["task"]

        state = await store.get_schedule_state("task")
        occurrence_id = build_occurrence_id(
            ScheduleType.ONCE, state.schedule_revision, state.next_due_at
        )
        due = await store.get_due_schedules(datetime.now(timezone.utc), limit=10)
        assert [(task.task_id, occurrence) for task, _state, occurrence in due] == [
            ("task", occurrence_id)
        ]

        advanced = await store.advance_schedule(
            "task",
            expected_revision=state.schedule_revision,
            occurrence_id=occurrence_id,
            next_due_at=None,
        )
        assert advanced.last_due_at == state.next_due_at
        assert advanced.next_due_at is None

        run_order_start = datetime.now(timezone.utc)
        first = await store.create_run_with_overlap_guard(
            background_run(
                occurrence_id=occurrence_id,
                queued_at=run_order_start,
            ),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        second = await store.create_run_with_overlap_guard(
            background_run(
                "task",
                occurrence_id="manual-second",
                queued_at=run_order_start + timedelta(seconds=1),
            ),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        assert [run.run_id for run in await store.list_runs()] == [
            first.run_id,
            second.run_id,
        ]
    finally:
        await close_store(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_scheduled_dispatch_is_idempotent_and_advances_state(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        await store.save_task(
            task_spec(schedule={"type": "interval", "seconds": 60})
        )
        state = await store.get_schedule_state("task")
        due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        state = state.model_copy(update={"next_due_at": due_at}, deep=True)
        await store.save_schedule_state(state)

        occurrence_id = build_occurrence_id(
            ScheduleType.INTERVAL, state.schedule_revision, due_at
        )
        next_due_at = due_at + timedelta(seconds=60)
        scheduled_run = background_run(
            occurrence_id=occurrence_id,
            trigger_type=TriggerType.INTERVAL,
            due_at=due_at,
            queued_at=due_at,
        )

        created = await store.dispatch_scheduled_run(
            scheduled_run,
            OverlapPolicy.ALLOW_PARALLEL,
            expected_schedule_revision=state.schedule_revision,
            next_due_at=next_due_at,
        )

        assert created.status == RunStatus.QUEUED
        updated_state = await store.get_schedule_state("task")
        assert updated_state.schedule_revision == state.schedule_revision
        assert updated_state.last_due_at == due_at
        assert updated_state.next_due_at == next_due_at
        assert updated_state.last_dispatched_at is not None

        duplicate = await store.dispatch_scheduled_run(
            background_run(
                occurrence_id=occurrence_id,
                trigger_type=TriggerType.INTERVAL,
                due_at=due_at,
                queued_at=due_at + timedelta(seconds=1),
            ),
            OverlapPolicy.ALLOW_PARALLEL,
            expected_schedule_revision=state.schedule_revision,
            next_due_at=next_due_at + timedelta(seconds=60),
        )
        assert duplicate.run_id == created.run_id
        assert [run.run_id for run in await store.list_runs()] == [created.run_id]
        duplicate_state = await store.get_schedule_state("task")
        assert duplicate_state.next_due_at == updated_state.next_due_at
        assert duplicate_state.last_due_at == updated_state.last_due_at
        assert duplicate_state.last_dispatched_at == updated_state.last_dispatched_at
        assert duplicate_state.updated_at == updated_state.updated_at

        with pytest.raises(TaskStoreError):
            await store.dispatch_scheduled_run(
                background_run(
                    occurrence_id="wrong-revision",
                    trigger_type=TriggerType.INTERVAL,
                    due_at=next_due_at,
                ),
                OverlapPolicy.ALLOW_PARALLEL,
                expected_schedule_revision=state.schedule_revision + 1,
                next_due_at=next_due_at + timedelta(seconds=60),
            )
    finally:
        await close_store(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_active_runs_and_cancel_previous(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        await store.save_task(
            task_spec(overlap_policy=OverlapPolicy.CANCEL_PREVIOUS)
        )
        await store.save_task(task_spec("other"))

        queued_at = datetime.now(timezone.utc)
        first = await store.create_run_with_overlap_guard(
            background_run(
                occurrence_id="first",
                queued_at=queued_at,
            ),
            OverlapPolicy.CANCEL_PREVIOUS,
        )
        second = await store.create_run_with_overlap_guard(
            background_run(
                occurrence_id="second",
                queued_at=queued_at + timedelta(seconds=1),
            ),
            OverlapPolicy.CANCEL_PREVIOUS,
        )
        other = await store.create_run_with_overlap_guard(
            background_run(
                "other",
                occurrence_id="other",
                queued_at=queued_at + timedelta(seconds=2),
            ),
            OverlapPolicy.ALLOW_PARALLEL,
        )

        cancelled_first = await store.get_run(first.run_id)
        assert cancelled_first.cancel_requested_at is not None
        assert await store.is_cancel_requested(first.run_id) is True
        assert [run.run_id for run in await store.list_active_runs("task")] == [
            first.run_id,
            second.run_id,
        ]

        await store.transition_run(first.run_id, {RunStatus.QUEUED}, RunStatus.CANCELLED)
        assert [run.run_id for run in await store.list_active_runs("task")] == [
            second.run_id
        ]
        assert [run.run_id for run in await store.list_active_runs()] == [
            second.run_id,
            other.run_id,
        ]
    finally:
        await close_store(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_deterministic_indexes_and_work_queues(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        created_at = datetime.now(timezone.utc)
        due_at = created_at - timedelta(seconds=1)
        task_a = task_spec(
            "task-a",
            schedule={"type": "once", "run_at": due_at},
        ).model_copy(
            update={"created_at": created_at, "updated_at": created_at},
            deep=True,
        )
        task_b = task_spec(
            "task-b",
            schedule={"type": "once", "run_at": due_at + timedelta(seconds=1)},
        ).model_copy(
            update={
                "created_at": created_at + timedelta(seconds=1),
                "updated_at": created_at + timedelta(seconds=1),
            },
            deep=True,
        )
        await store.save_task(task_b)
        await store.save_task(task_a)

        assert [task.task_id for task in await store.list_tasks()] == [
            "task-a",
            "task-b",
        ]
        due = await store.get_due_schedules(datetime.now(timezone.utc), limit=10)
        assert [task.task_id for task, _state, _occurrence in due] == [
            "task-a",
            "task-b",
        ]

        later = await store.create_run_with_overlap_guard(
            background_run(
                "task-a",
                occurrence_id="later",
                queued_at=created_at - timedelta(seconds=5),
            ),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        earlier = await store.create_run_with_overlap_guard(
            background_run(
                "task-b",
                occurrence_id="earlier",
                queued_at=created_at - timedelta(seconds=10),
            ),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        assert [run.run_id for run in await store.list_claimable_runs(limit=10)] == [
            earlier.run_id,
            later.run_id,
        ]

        claimed = await store.claim_run(earlier.run_id, "worker", lease_seconds=30)
        await store.create_attempt(
            BackgroundAttempt(
                run_id=claimed.run_id,
                attempt_number=2,
                worker_id="worker",
                lease_token=claimed.lease_token,
            )
        )
        await store.create_attempt(
            BackgroundAttempt(
                run_id=claimed.run_id,
                attempt_number=1,
                worker_id="worker",
                lease_token=claimed.lease_token,
            )
        )
        assert [
            attempt.attempt_number for attempt in await store.list_attempts(claimed.run_id)
        ] == [1, 2]
    finally:
        await close_store(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_claim_lease_attempt_and_cancel(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        await store.save_task(task_spec())
        created = await store.create_run_with_overlap_guard(
            background_run(), OverlapPolicy.ALLOW_PARALLEL
        )

        claimed = await store.claim_next_run("worker", lease_seconds=30)
        assert claimed.run_id == created.run_id
        assert claimed.lease_owner == "worker"
        assert claimed.lease_token
        assert await store.claim_next_run("other", lease_seconds=30) is None

        with pytest.raises(RunLeaseError):
            await store.transition_run(
                claimed.run_id,
                {RunStatus.CLAIMED},
                RunStatus.RUNNING,
                worker_id="other",
                lease_token=claimed.lease_token,
            )

        running = await store.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            {"attempt": 1},
            "worker",
            claimed.lease_token,
        )
        await store.refresh_lease(running.run_id, "worker", running.lease_token, 30)
        updated = await store.update_run_metadata(
            running.run_id,
            {"phase": "running"},
            worker_id="worker",
            lease_token=running.lease_token,
        )
        assert updated.metadata["phase"] == "running"

        attempt = BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="worker",
            lease_token=running.lease_token,
        )
        await store.create_attempt(attempt)
        completed_attempt = await store.update_attempt(
            attempt.attempt_id,
            {"status": AttemptStatus.COMPLETED},
            "worker",
            running.lease_token,
        )
        assert completed_attempt.status == AttemptStatus.COMPLETED

        await store.request_cancel(running.run_id)
        assert await store.is_cancel_requested(running.run_id) is True
        with pytest.raises(RunCancellationRequestedError):
            await store.transition_run(
                running.run_id,
                {RunStatus.RUNNING},
                RunStatus.COMPLETED,
                worker_id="worker",
                lease_token=running.lease_token,
            )

        cancelled = await store.transition_run(
            running.run_id,
            {RunStatus.RUNNING},
            RunStatus.CANCELLED,
            worker_id="worker",
            lease_token=running.lease_token,
        )
        assert cancelled.status == RunStatus.CANCELLED
        assert cancelled.finished_at is not None

        with pytest.raises(TaskStoreError):
            await store.transition_run(
                running.run_id,
                {RunStatus.CANCELLED},
                RunStatus.QUEUED,
            )
    finally:
        await close_store(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", SHARED_CONTRACT_BACKENDS)
async def test_task_store_contract_overlap_queue_and_expired_lease(
    store_kind, tmp_path
):
    store = await create_store(store_kind, tmp_path)
    try:
        await store.save_agent(agent_spec())
        await store.save_task(
            task_spec(overlap_policy=OverlapPolicy.QUEUE_NEXT)
        )
        now = datetime.now(timezone.utc)
        first = await store.create_run_with_overlap_guard(
            background_run(
                "task",
                occurrence_id="first",
                queued_at=now - timedelta(seconds=2),
            ),
            OverlapPolicy.QUEUE_NEXT,
        )
        second = await store.create_run_with_overlap_guard(
            background_run(
                "task",
                occurrence_id="second",
                queued_at=now - timedelta(seconds=1),
            ),
            OverlapPolicy.QUEUE_NEXT,
        )

        claimed = await store.claim_next_run("worker-a", lease_seconds=-1)
        assert claimed.run_id == first.run_id
        claimable = await store.list_claimable_runs(limit=10)
        assert second.run_id not in {run.run_id for run in claimable}

        expired = await store.list_expired_leases(datetime.now(timezone.utc))
        assert [run.run_id for run in expired] == [first.run_id]

        stolen = await store.steal_expired_run(first.run_id, "worker-b", 30)
        assert stolen.lease_owner == "worker-b"
        assert stolen.lease_token != claimed.lease_token
        assert stolen.lease_generation == claimed.lease_generation + 1

        released = await store.transition_run(
            stolen.run_id,
            {RunStatus.CLAIMED},
            RunStatus.CANCELLED,
            worker_id="worker-b",
            lease_token=stolen.lease_token,
        )
        assert released.status == RunStatus.CANCELLED

        next_claim = await store.claim_next_run("worker-c", lease_seconds=30)
        assert next_claim.run_id == second.run_id
    finally:
        await close_store(store)


@pytest.mark.asyncio
async def test_sql_task_store_contract_persists_across_reconnect(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    first = SqlTaskStore(url=url)
    await first.initialize()
    await first.save_agent(agent_spec())
    await first.save_task(task_spec())
    run = await first.create_run_with_overlap_guard(
        background_run(), OverlapPolicy.ALLOW_PARALLEL
    )
    await first.close()

    restored = SqlTaskStore(url=url)
    await restored.initialize()
    try:
        assert (await restored.get_agent("agent")).agent_id == "agent"
        assert (await restored.get_task("task")).task_id == "task"
        assert (await restored.get_run(run.run_id)).run_id == run.run_id
    finally:
        await restored.close()


async def cleanup_redis_prefix(store: RedisTaskStore) -> None:
    try:
        client = store._require_client()
        keys = [key async for key in client.scan_iter(f"{store.prefix}:*")]
        if keys:
            await client.delete(*keys)
    except Exception:
        pass


def redis_contract_url() -> str:
    return os.getenv(REDIS_CONTRACT_URL_ENV, DEFAULT_REDIS_CONTRACT_URL)


def redis_unavailable_message(exc: Exception) -> str:
    return (
        "Redis task store unavailable. To run live Redis contract tests, "
        f"set {REDIS_CONTRACT_URL_ENV}, or start a local Redis server at "
        f"{DEFAULT_REDIS_CONTRACT_URL}. Configured URL: "
        f"{_configured_value_label(redis_contract_url())}. "
        f"Error: {_summarize_exception(exc)}"
    )


def mongodb_contract_uri() -> str:
    return os.getenv(MONGODB_CONTRACT_URI_ENV, DEFAULT_MONGODB_CONTRACT_URI)


def mongodb_contract_database() -> str:
    return os.getenv(MONGODB_CONTRACT_DATABASE_ENV, DEFAULT_MONGODB_CONTRACT_DATABASE)


def mongodb_unavailable_message(exc: Exception) -> str:
    return (
        "MongoDB task store unavailable. To run live MongoDB contract tests, "
        f"set {MONGODB_CONTRACT_URI_ENV} and {MONGODB_CONTRACT_DATABASE_ENV}, "
        f"or start a local MongoDB server at {DEFAULT_MONGODB_CONTRACT_URI}. "
        f"Configured URI: {_configured_value_label(mongodb_contract_uri())}; database: "
        f"{mongodb_contract_database()}. Error: {_summarize_exception(exc)}"
    )


def _configured_value_label(value: str) -> str:
    return "default" if value.startswith(("redis://localhost", "mongodb://localhost")) else "provided"


def _summarize_exception(exc: Exception) -> str:
    message = str(exc).splitlines()[0]
    message = message.split(", Topology Description:", 1)[0]
    return f"{type(exc).__name__}: {message[:240]}"


@pytest.mark.asyncio
async def test_redis_task_store_contract_persists_across_reconnect():
    skip_if_backend_unavailable("redis")
    prefix = f"test:omnicoreagent:background:{uuid4().hex}"
    url = redis_contract_url()
    first = RedisTaskStore(
        url=url,
        prefix=prefix,
        connect_timeout=REDIS_CONTRACT_CONNECT_TIMEOUT,
        lock_timeout=0.5,
    )
    try:
        await first.initialize()
    except Exception as exc:
        await first.close()
        skip_backend("redis", redis_unavailable_message(exc))

    try:
        await first.save_agent(agent_spec())
        await save_mutated_schedule_state(first)
        schedule_state = await first.get_schedule_state("task")
        run = await first.create_run_with_overlap_guard(
            background_run(), OverlapPolicy.ALLOW_PARALLEL
        )
        claimed = await first.claim_next_run("worker", lease_seconds=30)
        running = await first.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            {"attempt": 1},
            "worker",
            claimed.lease_token,
        )
        attempt = BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="worker",
            lease_token=running.lease_token,
        )
        await first.create_attempt(attempt)
        await first.update_attempt(
            attempt.attempt_id,
            {"status": AttemptStatus.COMPLETED},
            "worker",
            running.lease_token,
        )
        completed = await first.transition_run(
            running.run_id,
            {RunStatus.RUNNING},
            RunStatus.COMPLETED,
            {"result_preview": "done"},
            "worker",
            running.lease_token,
        )
        assert completed.run_id == run.run_id

        cancel_requested = await first.create_run_with_overlap_guard(
            background_run(occurrence_id="cancel-requested"),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        await first.request_cancel(cancel_requested.run_id)

        restored = RedisTaskStore(
            url=url,
            prefix=prefix,
            connect_timeout=REDIS_CONTRACT_CONNECT_TIMEOUT,
            lock_timeout=0.5,
        )
        await restored.initialize()
        try:
            assert (await restored.get_agent("agent")).agent_id == "agent"
            assert (await restored.get_task("task")).task_id == "task"
            restored_state = await restored.get_schedule_state("task")
            assert restored_state.task_id == schedule_state.task_id
            assert restored_state.next_due_at == schedule_state.next_due_at
            assert restored_state.last_due_at == schedule_state.last_due_at
            assert restored_state.last_dispatched_at == schedule_state.last_dispatched_at
            assert restored_state.paused == schedule_state.paused
            assert restored_state.schedule_revision == schedule_state.schedule_revision
            assert (await restored.get_run(run.run_id)).status == RunStatus.COMPLETED
            attempts = await restored.list_attempts(run.run_id)
            assert [(item.attempt_number, item.status) for item in attempts] == [
                (1, AttemptStatus.COMPLETED)
            ]
            assert await restored.is_cancel_requested(cancel_requested.run_id) is True
            restored_cancelled = await restored.get_run(cancel_requested.run_id)
            assert restored_cancelled.cancel_requested_at is not None
        finally:
            await restored.close()
    finally:
        try:
            await cleanup_redis_prefix(first)
        finally:
            await first.close()


async def cleanup_mongodb_prefix(store: MongoDbTaskStore) -> None:
    try:
        db = store._db
        if db is None:
            return
        collection_names = await db.list_collection_names()
        for name in collection_names:
            if name.startswith(f"{store.collection_prefix}_"):
                await db.drop_collection(name)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_mongodb_task_store_contract_persists_across_reconnect():
    skip_if_backend_unavailable("mongodb")
    collection_prefix = f"test_omnicoreagent_background_{uuid4().hex}"
    first = MongoDbTaskStore(
        uri=mongodb_contract_uri(),
        database=mongodb_contract_database(),
        collection_prefix=collection_prefix,
        connect_timeout=5,
        lock_timeout=0.5,
    )
    try:
        await first.initialize()
    except Exception as exc:
        await first.close()
        skip_backend("mongodb", mongodb_unavailable_message(exc))

    try:
        await first.save_agent(agent_spec())
        await save_mutated_schedule_state(first)
        schedule_state = await first.get_schedule_state("task")
        run = await first.create_run_with_overlap_guard(
            background_run(), OverlapPolicy.ALLOW_PARALLEL
        )
        claimed = await first.claim_next_run("worker", lease_seconds=30)
        running = await first.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            {"attempt": 1},
            "worker",
            claimed.lease_token,
        )
        attempt = BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="worker",
            lease_token=running.lease_token,
        )
        await first.create_attempt(attempt)
        await first.update_attempt(
            attempt.attempt_id,
            {"status": AttemptStatus.COMPLETED},
            "worker",
            running.lease_token,
        )
        completed = await first.transition_run(
            running.run_id,
            {RunStatus.RUNNING},
            RunStatus.COMPLETED,
            {"result_preview": "done"},
            "worker",
            running.lease_token,
        )
        assert completed.run_id == run.run_id

        cancel_requested = await first.create_run_with_overlap_guard(
            background_run(occurrence_id="cancel-requested"),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        await first.request_cancel(cancel_requested.run_id)

        restored = MongoDbTaskStore(
            uri=mongodb_contract_uri(),
            database=mongodb_contract_database(),
            collection_prefix=collection_prefix,
            connect_timeout=5,
            lock_timeout=0.5,
        )
        await restored.initialize()
        try:
            assert (await restored.get_agent("agent")).agent_id == "agent"
            assert (await restored.get_task("task")).task_id == "task"
            restored_state = await restored.get_schedule_state("task")
            assert restored_state.task_id == schedule_state.task_id
            assert restored_state.next_due_at == schedule_state.next_due_at
            assert restored_state.last_due_at == schedule_state.last_due_at
            assert restored_state.last_dispatched_at == schedule_state.last_dispatched_at
            assert restored_state.paused == schedule_state.paused
            assert restored_state.schedule_revision == schedule_state.schedule_revision
            assert (await restored.get_run(run.run_id)).status == RunStatus.COMPLETED
            attempts = await restored.list_attempts(run.run_id)
            assert [(item.attempt_number, item.status) for item in attempts] == [
                (1, AttemptStatus.COMPLETED)
            ]
            assert await restored.is_cancel_requested(cancel_requested.run_id) is True
            restored_cancelled = await restored.get_run(cancel_requested.run_id)
            assert restored_cancelled.cancel_requested_at is not None
        finally:
            await restored.close()
    finally:
        try:
            await cleanup_mongodb_prefix(first)
        finally:
            await first.close()
