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
) -> BackgroundRun:
    return BackgroundRun(
        task_id=task_id,
        agent_id="agent",
        query_snapshot=f"snapshot {task_id}",
        trigger_type=TriggerType.MANUAL,
        session_id=f"session-{task_id}",
        workspace_path=f"background/agent/{task_id}/run",
        occurrence_id=occurrence_id,
        queued_at=queued_at,
    )


async def create_store(kind: str, tmp_path):
    if kind == "in_memory":
        store = InMemoryTaskStore()
    elif kind == "sql":
        store = SqlTaskStore(url=f"sqlite:///{tmp_path / 'background.db'}")
    else:  # pragma: no cover
        raise AssertionError(f"unknown store kind: {kind}")
    await store.initialize()
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["in_memory", "sql"])
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

        first = await store.create_run_with_overlap_guard(
            background_run(occurrence_id=occurrence_id),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        second = await store.create_run_with_overlap_guard(
            background_run("task", occurrence_id="manual-second"),
            OverlapPolicy.ALLOW_PARALLEL,
        )
        assert [run.run_id for run in await store.list_runs()] == [
            first.run_id,
            second.run_id,
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["in_memory", "sql"])
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
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["in_memory", "sql"])
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
        await store.close()


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


@pytest.mark.asyncio
async def test_redis_task_store_contract_persists_across_reconnect():
    prefix = f"test:omnicoreagent:background:{uuid4().hex}"
    url = redis_contract_url()
    first = RedisTaskStore(
        url=url,
        prefix=prefix,
        connect_timeout=0.2,
        lock_timeout=0.5,
    )
    try:
        await first.initialize()
    except Exception as exc:
        pytest.skip(f"Redis task store unavailable: {exc}")

    try:
        await first.save_agent(agent_spec())
        await first.save_task(task_spec())
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
        completed = await first.transition_run(
            running.run_id,
            {RunStatus.RUNNING},
            RunStatus.COMPLETED,
            {"result_preview": "done"},
            "worker",
            running.lease_token,
        )
        assert completed.run_id == run.run_id

        restored = RedisTaskStore(
            url=url,
            prefix=prefix,
            connect_timeout=0.2,
            lock_timeout=0.5,
        )
        await restored.initialize()
        try:
            assert (await restored.get_agent("agent")).agent_id == "agent"
            assert (await restored.get_task("task")).task_id == "task"
            assert (await restored.get_run(run.run_id)).status == RunStatus.COMPLETED
        finally:
            await restored.close()
    finally:
        try:
            await cleanup_redis_prefix(first)
        finally:
            await first.close()
