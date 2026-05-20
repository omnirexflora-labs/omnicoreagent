import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import json
import time

import pytest

from omnicoreagent.core.workspace.manager import Workspace
from omnicoreagent.background import (
    BackgroundAgentManager,
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
    ScheduleSpec,
    SqlTaskStore,
    TaskNotFoundError,
    TaskStoreError,
    TaskStoreRouter,
)
from omnicoreagent.background.models import (
    AttemptReason,
    AttemptStatus,
    BackoffPolicy,
    MisfirePolicy,
    ScheduleType,
    TriggerType,
    build_occurrence_id,
    build_session_id,
    build_workspace_path,
    deterministic_jitter_seconds,
    initial_schedule_due,
    next_cron_due,
    next_schedule_due,
)
from omnicoreagent.background.event_log import BackgroundEventLog
from omnicoreagent.background.recovery import BackgroundRunRecovery
from omnicoreagent.background.transitions import BackgroundRunTransitions
from omnicoreagent.core.telemetry import TelemetryStreamScope


class FakeAgent:
    name = "fake"
    system_instruction = "test"
    model_config = {"provider": "openai", "model": "gpt-4o-mini"}
    agent_config = {}

    def __init__(self, response="done", fail_times=0, delay=0):
        self.response = response
        self.fail_times = fail_times
        self.delay = delay
        self.calls = []

    async def run(self, query: str, session_id: str, run_id: str | None = None):
        self.calls.append({"query": query, "session_id": session_id, "run_id": run_id})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("planned failure")
        return {"response": self.response, "session_id": session_id}


class BlockingAgent(FakeAgent):
    async def run(self, query: str, session_id: str, run_id: str | None = None):
        self.calls.append({"query": query, "session_id": session_id, "run_id": run_id})
        if self.delay:
            time.sleep(self.delay)
        return {"response": self.response, "session_id": session_id}


class KwargsAgent(FakeAgent):
    async def run(self, query: str, session_id: str, **kwargs):
        self.calls.append(
            {"query": query, "session_id": session_id, "run_id": kwargs.get("run_id")}
        )
        return {"response": self.response, "session_id": session_id}


class NoRunIdAgent(FakeAgent):
    async def run(self, query: str, session_id: str):
        self.calls.append({"query": query, "session_id": session_id})
        return {"response": self.response, "session_id": session_id}


class ConfiguredFakeAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.mcp_tools = [{"name": "filesystem", "transport": "stdio"}]
        self.agent_config = {
            "enable_workspace_files": True,
            "workspace_config": {"workspace_backend": "local", "workspace_dir": "custom"},
        }


class CountingStore(InMemoryTaskStore):
    def __init__(self):
        super().__init__()
        self.refresh_count = 0

    async def refresh_lease(self, run_id, worker_id, lease_token, lease_seconds):
        self.refresh_count += 1
        await super().refresh_lease(run_id, worker_id, lease_token, lease_seconds)


class BrokenWorkspaceFiles:
    def write_text(self, path, text):
        raise RuntimeError("workspace write failed")

    def append_text(self, path, text):
        raise RuntimeError("workspace append failed")

    def read_text(self, path):
        raise RuntimeError("workspace read failed")

    def list_files(self, path):
        return []


class BrokenWorkspace:
    files = BrokenWorkspaceFiles()


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.sets = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def smembers(self, key):
        return self.sets.get(key, set())

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)
            self.sets.pop(key, None)
        return len(keys)

    async def eval(self, script, numkeys, *args):
        if "PEXPIRE" in script:
            key, token, _lease_ms = args
            return 1 if self.values.get(key) == token else 0
        if "SET" in script and numkeys == 2:
            lock_key, active_generation_key, token, generation = args
            if self.values.get(lock_key) == token:
                self.values[active_generation_key] = generation
                return 1
            return 0
        if "SET" in script and numkeys == 3:
            (
                lock_key,
                active_generation_key,
                previous_generation_key,
                token,
                generation,
                previous_generation,
            ) = args
            if self.values.get(lock_key) == token:
                self.values[active_generation_key] = generation
                if previous_generation:
                    self.values[previous_generation_key] = previous_generation
                return 1
            return 0
        key, token = args
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


class FakeMongoUpdateResult:
    def __init__(self, matched_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.upserted_id = upserted_id


class FakeMongoCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, filter):
        return self.docs.get(filter["_id"])

    async def replace_one(self, filter, document, upsert=False):
        self.docs[filter["_id"]] = document
        return FakeMongoUpdateResult(matched_count=1)

    def find(self, filter):
        class Cursor:
            def __init__(self, docs):
                self.docs = docs

            async def to_list(self, length=None):
                return self.docs

        generation = filter.get("_generation")
        return Cursor(
            [doc for doc in self.docs.values() if doc.get("_generation") == generation]
        )

    async def insert_one(self, document):
        if document["_id"] in self.docs:
            raise ValueError(f"duplicate document id: {document['_id']}")
        self.docs[document["_id"]] = document

    async def insert_many(self, documents):
        for document in documents:
            if document["_id"] in self.docs:
                raise ValueError(f"duplicate document id: {document['_id']}")
            self.docs[document["_id"]] = document

    async def delete_one(self, filter):
        deleted = self.docs.pop(filter["_id"], None)
        return FakeMongoUpdateResult(matched_count=1 if deleted else 0)

    async def delete_many(self, filter):
        if "_generation" in filter:
            generation = filter["_generation"]
            to_delete = [
                doc_id
                for doc_id, doc in self.docs.items()
                if doc.get("_generation") == generation
            ]
        else:
            to_delete = [
                doc_id
                for doc_id, doc in self.docs.items()
                if all(doc.get(key) == value for key, value in filter.items())
            ]
        for doc_id in to_delete:
            self.docs.pop(doc_id, None)
        return FakeMongoUpdateResult(matched_count=len(to_delete))

    async def update_one(self, filter, update, upsert=False):
        doc_id = filter["_id"]
        doc = self.docs.get(doc_id)
        if doc is None:
            if not upsert:
                return FakeMongoUpdateResult()
            doc = {"_id": doc_id}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[doc_id] = doc
            return FakeMongoUpdateResult(upserted_id=doc_id)

        if "$or" in filter and not any(
            self._matches(doc, condition) for condition in filter["$or"]
        ):
            return FakeMongoUpdateResult()
        if "token" in filter and doc.get("token") != filter["token"]:
            return FakeMongoUpdateResult()
        doc.update(update.get("$set", {}))
        self.docs[doc_id] = doc
        return FakeMongoUpdateResult(matched_count=1)

    def _matches(self, doc, condition):
        if condition == {"token": None}:
            return doc.get("token") is None
        if condition == {"token": {"$exists": False}}:
            return "token" not in doc
        if "expires_at" in condition:
            return doc.get("expires_at") <= condition["expires_at"]["$lte"]
        if "token" in condition:
            return doc.get("token") == condition["token"]
        return False


class FakeMongoDb:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeMongoCollection())

    def with_options(self, **kwargs):
        return self


class FakeRedisTaskStore(RedisTaskStore):
    def __init__(self, client):
        super().__init__(url="redis://localhost:6379", prefix="lazy")
        self.client = client
        self.initialize_count = 0
        self.close_count = 0

    async def initialize(self):
        self.initialize_count += 1
        self._client = self.client
        await self._load_backend_state()

    async def close(self):
        self.close_count += 1
        self._client = None


class FakeMongoTaskStore(MongoDbTaskStore):
    def __init__(self, db):
        super().__init__(uri="mongodb://localhost:27017", database="test")
        self.db = db
        self.initialize_count = 0
        self.close_count = 0

    async def initialize(self):
        self.initialize_count += 1
        self._db = self.db
        await self._lock_collection.update_one(
            {"_id": "task_store"},
            {"$setOnInsert": {"token": None, "expires_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        await self._load_backend_state()

    async def close(self):
        self.close_count += 1
        self._db = None


class CancellingAttemptStore(InMemoryTaskStore):
    async def create_attempt(self, attempt):
        await super().create_attempt(attempt)
        await self.request_cancel(attempt.run_id)


class CancellingCompletedAttemptStore(InMemoryTaskStore):
    async def update_attempt(self, attempt_id, patch, worker_id=None, lease_token=None):
        updated = await super().update_attempt(
            attempt_id,
            patch,
            worker_id,
            lease_token,
        )
        if patch.get("status") == AttemptStatus.COMPLETED:
            await self.request_cancel(updated.run_id)
        return updated


class CancellingFailedAttemptStore(InMemoryTaskStore):
    def __init__(self, cancel_on_retry_delay=False):
        super().__init__()
        self.cancel_on_retry_delay = cancel_on_retry_delay

    async def update_attempt(self, attempt_id, patch, worker_id=None, lease_token=None):
        updated = await super().update_attempt(
            attempt_id,
            patch,
            worker_id,
            lease_token,
        )
        if patch.get("status") in {AttemptStatus.FAILED, AttemptStatus.TIMEOUT} or (
            self.cancel_on_retry_delay and "retry_delay_seconds" in patch
        ):
            await self.request_cancel(updated.run_id)
        return updated


class CancellingBeforeCompletedTransitionStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if next_status == RunStatus.COMPLETED:
            await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancellingBeforeRetryTransitionStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if next_status == RunStatus.RETRYING:
            await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancellingBeforeRetryRequeueStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if next_status == RunStatus.QUEUED:
            latest = await self.get_run(run_id)
            if latest and latest.status == RunStatus.RETRYING:
                await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancellingBeforeRecoveryRetryingStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if next_status == RunStatus.RETRYING:
            await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancellingBeforeRecoveryRequeueStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if next_status == RunStatus.QUEUED:
            await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancellingBeforeClaimReleaseStore(InMemoryTaskStore):
    async def transition_run(
        self,
        run_id,
        expected,
        next_status,
        patch=None,
        worker_id=None,
        lease_token=None,
    ):
        if expected == {RunStatus.CLAIMED} and next_status == RunStatus.QUEUED:
            await self.request_cancel(run_id)
        return await super().transition_run(
            run_id,
            expected,
            next_status,
            patch,
            worker_id,
            lease_token,
        )


class CancelOnStartedManager(BackgroundAgentManager):
    async def _emit_run(self, event_name, run, **extra_payload):
        await super()._emit_run(event_name, run, **extra_payload)
        if event_name == "background_run_started":
            await self.cancel_run(run.run_id)


async def wait_for(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(0.01)
    result = predicate()
    if inspect.isawaitable(result):
        result = await result
    return result


def agent_spec():
    return BackgroundAgentSpec(agent_id="agent")


def task_spec(**overrides):
    data = {
        "task_id": "task",
        "agent_id": "agent",
        "query": "write report",
        "schedule": {"type": "manual"},
    }
    data.update(overrides)
    return BackgroundTaskSpec(**data)


@pytest.mark.parametrize(
    "bad_id",
    ["", "has space", "slash/id", "semi;colon"],
)
def test_model_rejects_unsafe_ids(bad_id):
    with pytest.raises(ValueError):
        BackgroundAgentSpec(agent_id=bad_id)


def test_schedule_validation():
    with pytest.raises(Exception):
        ScheduleSpec(type="interval", seconds=0)
    with pytest.raises(Exception):
        ScheduleSpec(type="manual", seconds=10)
    assert ScheduleSpec(type="interval", seconds=10).seconds == 10


def test_session_and_workspace_paths():
    task = task_spec()
    assert build_session_id(task, "run1") == "background:agent:task"
    assert build_workspace_path(task, "run1") == "background/agent/task/run1"


@pytest.mark.asyncio
async def test_in_memory_store_agent_task_and_schedule_state():
    store = InMemoryTaskStore()
    await store.initialize()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())

    assert (await store.get_agent("agent")).agent_id == "agent"
    assert (await store.get_task("task")).query == "write report"
    state = await store.get_schedule_state("task")
    assert state.task_id == "task"
    assert state.next_due_at is None


@pytest.mark.asyncio
async def test_due_schedule_and_atomic_dispatch_advances_state():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    task = task_spec(
        schedule={"type": "once", "run_at": due_at},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )
    await store.save_task(task)

    due = await store.get_due_schedules(datetime.now(timezone.utc), limit=10)
    assert len(due) == 1
    due_task, state, occurrence_id = due[0]
    run = BackgroundRun(
        task_id=due_task.task_id,
        agent_id=due_task.agent_id,
        query_snapshot=due_task.query,
        trigger_type=TriggerType.ONCE,
        due_at=state.next_due_at,
        occurrence_id=occurrence_id,
        session_id=build_session_id(due_task, "run_x"),
        workspace_path=build_workspace_path(due_task, "run_x"),
    )
    created = await store.dispatch_scheduled_run(
        run,
        due_task.overlap_policy,
        expected_schedule_revision=state.schedule_revision,
        next_due_at=None,
    )

    assert created.status == RunStatus.QUEUED
    assert (await store.get_schedule_state("task")).next_due_at is None
    duplicate = await store.dispatch_scheduled_run(
        run,
        due_task.overlap_policy,
        expected_schedule_revision=state.schedule_revision,
        next_due_at=None,
    )
    assert duplicate.run_id == created.run_id


@pytest.mark.asyncio
async def test_cron_schedule_gets_initial_due_time():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec(schedule={"type": "cron", "expression": "* * * * *"}))

    state = await store.get_schedule_state("task")

    assert state.next_due_at is not None


@pytest.mark.asyncio
async def test_overlap_skip_persists_skipped_run():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    task = task_spec()
    await store.save_task(task)
    first = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    second = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="two",
        trigger_type=TriggerType.MANUAL,
        session_id="s2",
        workspace_path="w2",
    )

    await store.create_run_with_overlap_guard(first, OverlapPolicy.ALLOW_PARALLEL)
    skipped = await store.create_run_with_overlap_guard(
        second, OverlapPolicy.SKIP_IF_RUNNING
    )

    assert skipped.status == RunStatus.SKIPPED
    assert skipped.finished_at is not None


@pytest.mark.asyncio
async def test_queue_next_waits_for_active_task_run():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec(overlap_policy=OverlapPolicy.QUEUE_NEXT))
    now = datetime.now(timezone.utc)
    first = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
        queued_at=now,
    )
    second = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="two",
        trigger_type=TriggerType.MANUAL,
        session_id="s2",
        workspace_path="w2",
        queued_at=now + timedelta(seconds=1),
    )
    await store.create_run_with_overlap_guard(first, OverlapPolicy.QUEUE_NEXT)
    await store.create_run_with_overlap_guard(second, OverlapPolicy.QUEUE_NEXT)

    claimed = await store.claim_next_run("worker", lease_seconds=30)
    assert claimed.run_id == first.run_id
    claimable = await store.list_claimable_runs(limit=10)
    assert all(run.run_id != second.run_id for run in claimable)


@pytest.mark.asyncio
async def test_store_rejects_duplicate_run_and_attempt_ids():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        run_id="run_duplicate",
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    with pytest.raises(Exception):
        await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)

    claimed = await store.claim_next_run("worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        worker_id="worker",
        lease_token=claimed.lease_token,
    )
    attempt = BackgroundAttempt(
        attempt_id="attempt_duplicate",
        run_id=running.run_id,
        attempt_number=1,
        worker_id="worker",
        lease_token=running.lease_token,
    )
    await store.create_attempt(attempt)
    with pytest.raises(Exception):
        await store.create_attempt(attempt)


@pytest.mark.asyncio
async def test_lease_token_required_for_execution_mutations():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    claimed = await store.claim_next_run("worker", lease_seconds=30)

    with pytest.raises(RunLeaseError):
        await store.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            worker_id="worker",
            lease_token="wrong",
        )

    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        worker_id="worker",
        lease_token=claimed.lease_token,
    )
    assert running.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_expired_lease_blocks_stale_worker_mutation():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    claimed = await store.claim_next_run("worker", lease_seconds=-1)

    with pytest.raises(RunLeaseError):
        await store.transition_run(
            claimed.run_id,
            {RunStatus.CLAIMED},
            RunStatus.RUNNING,
            worker_id="worker",
            lease_token=claimed.lease_token,
        )


@pytest.mark.asyncio
async def test_store_rejects_cancelled_non_terminal_transition():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    claimed = await store.claim_next_run("worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        worker_id="worker",
        lease_token=claimed.lease_token,
    )
    await store.request_cancel(running.run_id)

    with pytest.raises(RunCancellationRequestedError):
        await store.transition_run(
            running.run_id,
            {RunStatus.RUNNING},
            RunStatus.COMPLETED,
            worker_id="worker",
            lease_token=running.lease_token,
        )


@pytest.mark.asyncio
async def test_transition_controller_turns_cancelled_progress_into_terminal_cancel():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    claimed = await store.claim_next_run("worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        worker_id="worker",
        lease_token=claimed.lease_token,
    )
    attempt = BackgroundAttempt(
        run_id=running.run_id,
        attempt_number=1,
        worker_id="worker",
        lease_token=running.lease_token,
    )
    await store.create_attempt(attempt)

    emitted = []

    async def emit_run(event_name, run, **extra):
        emitted.append((event_name, run.status))

    current_worker = "stale_worker"
    current_lease_seconds = 1
    transitions = BackgroundRunTransitions(
        task_store=store,
        worker_id=lambda: current_worker,
        lease_seconds=lambda: current_lease_seconds,
        emit_run=emit_run,
    )
    current_worker = "worker"
    current_lease_seconds = 30

    await store.request_cancel(running.run_id)
    result = await transitions.transition_or_cancel(
        run=running,
        attempt=attempt,
        expected={RunStatus.RUNNING},
        next_status=RunStatus.COMPLETED,
    )

    assert result is None
    latest = await store.get_run(running.run_id)
    assert latest.status == RunStatus.CANCELLED
    attempts = await store.list_attempts(running.run_id)
    assert attempts[0].status == AttemptStatus.CANCELLED
    assert emitted == [("background_run_cancelled", RunStatus.CANCELLED)]


@pytest.mark.asyncio
async def test_manager_run_now_executes_agent_and_records_events():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert run.result_preview == "complete"
    assert agent.calls[0]["session_id"] == "background:agent:task"
    query = agent.calls[0]["query"]
    assert "workspace_path: /workspace/background/agent/task/" in query
    assert "/output.md" in query
    assert "/scratchpad/" in query
    assert "/logs/" in query
    assert "/artifacts/" in query
    assert "/subagents/" in query
    assert query.endswith("do work")
    events = await manager.get_run_events(run.run_id)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["event"] == "background_run_completed"
    assert "background_run_claimed" in {event["event"] for event in events}


@pytest.mark.asyncio
async def test_manager_status_payloads_are_inspectable_without_events():
    manager = BackgroundAgentManager(task_store="in_memory", worker_id="worker")
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "interval", "seconds": 60},
    )

    before = await manager.get_task_status("task")
    assert before["task_id"] == "task"
    assert before["agent_id"] == "agent"
    assert before["enabled"] is True
    assert before["runs"] == 0
    assert before["active_runs"] == 0
    assert before["latest_run"] is None
    assert before["schedule"]["type"] == "interval"
    assert before["schedule_state"]["next_due_at"] is not None

    run = await manager.run_now("task", wait=True)

    task_status = await manager.get_task_status("task")
    manager_status = await manager.get_manager_status()

    assert task_status["runs"] == 1
    assert task_status["active_runs"] == 0
    assert task_status["status_counts"]["completed"] == 1
    assert task_status["latest_run"]["run_id"] == run.run_id
    assert task_status["latest_run"]["status"] == "completed"
    assert manager_status["worker_id"] == "worker"
    assert manager_status["agents"] == 1
    assert manager_status["tasks"] == 1
    assert manager_status["runs"] == 1
    assert manager_status["active_runs"] == 0
    assert manager_status["status_counts"]["completed"] == 1


@pytest.mark.asyncio
async def test_background_run_events_replay_from_manager_cache():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)

    events = await manager.get_run_events(run.run_id)

    assert events[0]["event"] == "background_run_queued"
    assert events[-1]["event"] == "background_run_completed"
    assert events[-1]["run_id"] == run.run_id
    assert events[-1]["task_id"] == "task"
    assert events[-1]["status"] == RunStatus.COMPLETED.value
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_background_run_passes_run_id_to_agent():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert agent.calls[0]["run_id"] == run.run_id


@pytest.mark.asyncio
async def test_background_run_passes_run_id_to_kwargs_agent():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = KwargsAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert agent.calls[0]["run_id"] == run.run_id


@pytest.mark.asyncio
async def test_background_run_supports_agent_without_run_id_keyword():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = NoRunIdAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert agent.calls[0]["session_id"] == run.session_id
    assert "run_id" not in agent.calls[0]


@pytest.mark.asyncio
async def test_background_run_lifecycle_events_emit_to_telemetry():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)
    events = await manager.telemetry_stream.get_events_after(
        TelemetryStreamScope(run_id=run.run_id, session_id=run.session_id),
        None,
    )

    event_names = [
        event.event_type
        for event in events
        if event.event_type.startswith("background_")
    ]
    assert event_names == [
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_completed",
    ]
    assert events[-1].metadata["run_id"] == run.run_id
    trace = await manager.telemetry_store.get_trace(f"trace_background_{run.run_id}")
    assert trace is not None
    assert trace.run_id == run.run_id
    assert trace.status.value == "completed"
    assert trace.events[-1].event_type == "background_run_completed"
    assert any(
        event.event_type == "workspace_write"
        and event.output["path"].endswith(("run.json", "events.jsonl"))
        for event in trace.events
    )


@pytest.mark.asyncio
async def test_background_run_events_replay_from_workspace_when_cache_cleared(tmp_path):
    workspace = Workspace.from_config(workspace_dir=tmp_path).ensure()
    manager = BackgroundAgentManager(task_store="in_memory", workspace=workspace)
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)
    manager._events.clear()

    events = await manager.get_run_events(run.run_id)

    assert [(event["event"], event["sequence"]) for event in events] == [
        ("background_run_queued", 1),
        ("background_run_claimed", 2),
        ("background_run_started", 3),
        ("background_run_completed", 4),
    ]


@pytest.mark.asyncio
async def test_terminal_background_event_waits_for_pending_event_write():
    class FakeWorkspaceIO:
        def __init__(self):
            self.written_events = []

        def read_events(self, workspace_path):
            return []

        def append_event(self, event):
            self.written_events.append(event["event"])

    workspace_io = FakeWorkspaceIO()
    event_log = BackgroundEventLog(
        task_store=InMemoryTaskStore(),
        workspace_io=workspace_io,
        replay_timeout_seconds=1,
    )
    original_write_run_event = event_log.write_run_event
    pending_started = asyncio.Event()
    release_started = asyncio.Event()

    async def slow_write_run_event(event):
        if event["event"] == "background_run_started":
            pending_started.set()
            await release_started.wait()
        await original_write_run_event(event)

    event_log.write_run_event = slow_write_run_event

    payload = {
        "agent_id": "agent",
        "task_id": "task",
        "run_id": "run-terminal-order",
        "session_id": "session",
        "status": "running",
        "workspace_path": "background/task/run-terminal-order",
    }
    await event_log.emit("background_run_queued", **payload)
    await event_log.emit("background_run_started", **payload)
    await asyncio.wait_for(pending_started.wait(), timeout=1)

    terminal_task = asyncio.create_task(
        event_log.emit(
            "background_run_completed",
            **{**payload, "status": "completed"},
        )
    )
    await asyncio.sleep(0)
    assert not terminal_task.done()

    release_started.set()
    await asyncio.wait_for(terminal_task, timeout=1)

    assert workspace_io.written_events == [
        "background_run_queued",
        "background_run_started",
        "background_run_completed",
    ]


@pytest.mark.asyncio
async def test_terminal_background_event_does_not_write_gap_after_drain_timeout():
    class FakeWorkspaceIO:
        def __init__(self):
            self.written_events = []

        def read_events(self, workspace_path):
            return []

        def append_event(self, event):
            self.written_events.append(
                {"event": event["event"], "sequence": event["sequence"]}
            )

    workspace_io = FakeWorkspaceIO()
    event_log = BackgroundEventLog(
        task_store=InMemoryTaskStore(),
        workspace_io=workspace_io,
        replay_timeout_seconds=0.01,
    )
    original_write_run_event = event_log.write_run_event
    pending_started = asyncio.Event()

    async def stuck_write_run_event(event):
        if event["event"] == "background_run_started":
            pending_started.set()
            await asyncio.Event().wait()
        await original_write_run_event(event)

    event_log.write_run_event = stuck_write_run_event

    payload = {
        "agent_id": "agent",
        "task_id": "task",
        "run_id": "run-terminal-gap",
        "session_id": "session",
        "status": "running",
        "workspace_path": "background/task/run-terminal-gap",
    }
    await event_log.emit("background_run_queued", **payload)
    await event_log.emit("background_run_started", **payload)
    await asyncio.wait_for(pending_started.wait(), timeout=1)

    await event_log.emit(
        "background_run_completed",
        **{**payload, "status": "completed"},
    )

    assert workspace_io.written_events == [
        {"event": "background_run_queued", "sequence": 1}
    ]
    assert event_log.prepare_event_trace(workspace_io.written_events) == [
        {"event": "background_run_queued", "sequence": 1}
    ]


@pytest.mark.asyncio
async def test_background_event_sequence_continues_after_manager_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    workspace = Workspace.from_config(workspace_dir=tmp_path / "workspace").ensure()
    first = BackgroundAgentManager(
        task_store={"backend": "sql", "url": url},
        workspace=workspace,
    )
    await first.register_agent("agent", FakeAgent(response="first"))
    await first.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await first.run_now("task")

    second = BackgroundAgentManager(
        task_store={"backend": "sql", "url": url},
        workspace=workspace,
    )
    await second.register_agent("agent", FakeAgent(response="second"), replace=True)
    executed = await second._execute_run(run.run_id)
    completed = await second.get_run(run.run_id)

    assert executed is True
    assert completed.run_id == run.run_id
    events = await second.get_run_events(run.run_id)

    assert [(event["event"], event["sequence"]) for event in events] == [
        ("background_run_queued", 1),
        ("background_run_claimed", 2),
        ("background_run_started", 3),
        ("background_run_completed", 4),
    ]


@pytest.mark.asyncio
async def test_manager_run_now_wait_executes_only_created_run():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="first",
        agent_id="agent",
        query="first work",
        schedule={"type": "manual"},
    )
    await manager.register_task(
        task_id="second",
        agent_id="agent",
        query="second work",
        schedule={"type": "manual"},
    )
    first = await manager.run_now("first")

    second = await manager.run_now("second", wait=True)

    first_latest = await manager.get_run(first.run_id)
    assert first_latest.status == RunStatus.QUEUED
    assert second.status == RunStatus.COMPLETED
    assert agent.calls[0]["query"].endswith("second work")


@pytest.mark.asyncio
async def test_manager_run_now_wait_respects_queue_next_active_run():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.QUEUE_NEXT,
    )
    first = await manager.run_now("task")
    await manager.task_store.claim_run(first.run_id, "other_worker", 30)

    second = await manager.run_now("task", wait=True)

    assert second.status == RunStatus.QUEUED
    assert agent.calls == []


@pytest.mark.asyncio
async def test_manager_run_now_wait_respects_queue_next_earlier_queued_run():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.QUEUE_NEXT,
    )
    first = await manager.run_now("task")

    second = await manager.run_now("task", wait=True)

    assert first.status == RunStatus.QUEUED
    assert second.status == RunStatus.QUEUED
    assert agent.calls == []


@pytest.mark.asyncio
async def test_manager_run_now_wait_timeout_polls_queue_next_blocker():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.QUEUE_NEXT,
    )
    first = await manager.run_now("task")

    async def cancel_blocker():
        await asyncio.sleep(0.05)
        await manager.cancel_run(first.run_id)

    cancel_task = asyncio.create_task(cancel_blocker())
    try:
        second = await manager.run_now("task", wait=True, timeout_seconds=0.5)
    finally:
        await cancel_task

    assert second.status == RunStatus.COMPLETED
    assert agent.calls


@pytest.mark.asyncio
async def test_queue_next_equal_queued_at_uses_stable_ordering():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store)
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.QUEUE_NEXT,
    )
    queued_at = datetime.now(timezone.utc)
    run_a = await manager.run_now("task", wait=False)
    run_b = await manager.run_now("task", wait=False)
    async with store._lock:
        store._runs[run_a.run_id] = store._runs[run_a.run_id].model_copy(
            update={"queued_at": queued_at, "triggered_at": queued_at}
        )
        store._runs[run_b.run_id] = store._runs[run_b.run_id].model_copy(
            update={"queued_at": queued_at, "triggered_at": queued_at}
        )

    claimable = await store.list_claimable_runs(limit=10)

    assert [item.run_id for item in claimable] == [min(run_a.run_id, run_b.run_id)]


@pytest.mark.asyncio
async def test_manager_run_now_wait_timeout_does_not_block_on_running_agent():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent_delay = 1.0
    agent = FakeAgent(response="complete", delay=agent_delay)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    started = asyncio.get_running_loop().time()
    run = await manager.run_now("task", wait=True, timeout_seconds=0.01)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < agent_delay
    assert run.status in {RunStatus.QUEUED, RunStatus.CLAIMED, RunStatus.RUNNING}
    completed = await wait_for(
        lambda: manager.list_runs(status=RunStatus.COMPLETED),
        timeout=1.5,
    )
    assert completed[0].run_id == run.run_id


@pytest.mark.asyncio
async def test_inline_timeout_shutdown_honors_retry_policy():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store)
    agent = FakeAgent(response="complete", delay=0.2)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True, timeout_seconds=0.01)
    await wait_for(lambda: manager.list_attempts(run.run_id), timeout=0.5)
    await manager.shutdown()

    latest = await store.get_run(run.run_id)
    attempts = await store.list_attempts(run.run_id)
    assert latest.status == RunStatus.FAILED
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].error == "worker shutdown"


@pytest.mark.asyncio
async def test_inline_timeout_shutdown_requeues_retryable_run():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store)
    agent = FakeAgent(response="complete", delay=0.2)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    run = await manager.run_now("task", wait=True, timeout_seconds=0.01)
    await wait_for(lambda: manager.list_attempts(run.run_id), timeout=0.5)
    await manager.shutdown()

    latest = await store.get_run(run.run_id)
    attempts = await store.list_attempts(run.run_id)
    assert latest.status == RunStatus.QUEUED
    assert latest.lease_owner is None
    assert latest.lease_token is None
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].retry_delay_seconds == 0


@pytest.mark.asyncio
async def test_manager_run_now_missing_or_disabled_task_raises():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    with pytest.raises(TaskNotFoundError):
        await manager.run_now("missing")

    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    await manager.update_task("task", {"enabled": False})
    with pytest.raises(TaskNotFoundError):
        await manager.run_now("task")


@pytest.mark.asyncio
async def test_update_task_validates_nested_schedule_patch():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    updated = await manager.update_task("task", {"schedule": {"type": "interval", "seconds": 60}})
    state = await manager.task_store.get_schedule_state("task")

    assert updated.schedule.type == ScheduleType.INTERVAL
    assert state.next_due_at is not None


@pytest.mark.asyncio
async def test_manager_retries_failed_attempt():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="ok", fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    attempts = await manager.list_attempts(run.run_id)
    assert len(attempts) == 2
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].reason == AttemptReason.INITIAL
    assert attempts[1].status == AttemptStatus.COMPLETED
    assert attempts[1].reason == AttemptReason.RETRY
    assert [event["event"] for event in manager._events[run.run_id]] == [
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_retrying",
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_completed",
    ]


@pytest.mark.asyncio
async def test_manager_dispatches_due_schedule_from_worker_loop():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="scheduled")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="scheduled work",
        schedule={"type": "once", "run_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )

    await manager.start()
    completed = await wait_for(
        lambda: manager.list_runs(status=RunStatus.COMPLETED),
        timeout=0.5,
    )
    await manager.shutdown()

    assert len(completed) == 1
    assert completed[0].trigger_type == TriggerType.ONCE
    assert agent.calls
    events = await manager.get_run_events(completed[0].run_id)
    assert [event["event"] for event in events[:2]] == [
        "background_task_scheduled",
        "background_run_queued",
    ]
    assert events[0]["occurrence_id"] == completed[0].occurrence_id
    assert events[0]["due_at"] is not None


@pytest.mark.asyncio
async def test_duplicate_scheduled_dispatch_does_not_duplicate_run_events():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store)
    await manager.register_agent("agent", FakeAgent(response="scheduled"))
    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="scheduled work",
        schedule={"type": "once", "run_at": due_at},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )

    await manager._dispatch_due_schedules()
    state = await store.get_schedule_state("task")
    async with store._lock:
        store._schedule_states["task"] = state.model_copy(
            update={"next_due_at": due_at},
            deep=True,
        )
    await manager._dispatch_due_schedules()
    runs = await manager.list_runs(task_id="task")
    events = await manager.get_run_events(runs[0].run_id)

    assert len(runs) == 1
    assert [event["event"] for event in events] == [
        "background_task_scheduled",
        "background_run_queued",
    ]
    assert [event["sequence"] for event in events] == [1, 2]


@pytest.mark.asyncio
async def test_manual_overlap_skip_emits_skipped_event():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent(response="complete", delay=0.1))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.SKIP_IF_RUNNING,
    )
    first = await manager.run_now("task")
    second = await manager.run_now("task")

    events = await manager.get_run_events(second.run_id)

    assert first.status == RunStatus.QUEUED
    assert second.status == RunStatus.SKIPPED
    assert events[-1]["event"] == "background_run_skipped"
    assert events[-1]["status"] == RunStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_long_running_background_run_emits_heartbeat_events():
    manager = BackgroundAgentManager(task_store="in_memory", lease_seconds=1)
    agent = FakeAgent(response="complete", delay=0.35)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)
    events = await manager.get_run_events(run.run_id)
    heartbeats = [
        event for event in events if event["event"] == "background_run_heartbeat"
    ]

    assert run.status == RunStatus.COMPLETED
    assert heartbeats
    assert all(event["worker_id"] == manager.worker_id for event in heartbeats)
    assert all(event["heartbeat_at"] for event in heartbeats)
    assert all(event["lease_expires_at"] for event in heartbeats)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_cancel_running_same_worker_stops_active_agent_task():
    manager = BackgroundAgentManager(task_store="in_memory", lease_seconds=1)
    agent = FakeAgent(response="complete", delay=60)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    await manager.start()
    try:
        queued = await manager.run_now("task")
        running = await wait_for(
            lambda: manager.get_run(queued.run_id),
            timeout=1.0,
        )
        while running.status != RunStatus.RUNNING:
            await asyncio.sleep(0.01)
            running = await manager.get_run(queued.run_id)

        await manager.cancel_run(queued.run_id)
        terminal = await manager.wait_for_run(queued.run_id, timeout_seconds=1)
        attempts = await manager.list_attempts(queued.run_id)

        assert terminal.status == RunStatus.CANCELLED
        assert attempts[-1].status == AttemptStatus.CANCELLED
        assert queued.run_id not in manager._active_agent_tasks
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_cancel_requested_before_agent_task_starts_marks_cancelled():
    store = CancellingAttemptStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert attempts[-1].status == AttemptStatus.CANCELLED
    assert agent.calls == []
    assert queued.run_id not in manager._active_agent_tasks


@pytest.mark.asyncio
async def test_cancel_during_start_event_before_agent_task_starts_marks_cancelled():
    manager = CancelOnStartedManager(task_store="in_memory", lease_seconds=1)
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert attempts[-1].status == AttemptStatus.CANCELLED
    assert agent.calls == []
    assert queued.run_id not in manager._active_agent_tasks


@pytest.mark.asyncio
async def test_cancel_after_agent_result_before_completion_marks_cancelled():
    store = CancellingCompletedAttemptStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert attempts[-1].status == AttemptStatus.CANCELLED
    assert agent.calls
    assert queued.run_id not in manager._active_agent_tasks


@pytest.mark.asyncio
async def test_cancel_before_completed_transition_marks_cancelled():
    store = CancellingBeforeCompletedTransitionStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)
    events = await manager.get_run_events(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert attempts[-1].status == AttemptStatus.CANCELLED
    assert [event["event"] for event in events][-1] == "background_run_cancelled"


@pytest.mark.asyncio
async def test_cancel_after_failed_attempt_blocks_retry_requeue():
    store = CancellingFailedAttemptStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)
    events = await manager.get_run_events(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert len(attempts) == 1
    assert attempts[-1].status == AttemptStatus.CANCELLED
    assert [event["event"] for event in events] == [
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_cancelled",
    ]


@pytest.mark.asyncio
async def test_cancel_after_retry_delay_blocks_retry_requeue():
    store = CancellingFailedAttemptStore(cancel_on_retry_delay=True)
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert len(attempts) == 1
    assert attempts[-1].status == AttemptStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_before_retrying_transition_blocks_retry_requeue():
    store = CancellingBeforeRetryTransitionStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert len(attempts) == 1
    assert attempts[-1].status == AttemptStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_before_retry_requeue_transition_blocks_retry_requeue():
    store = CancellingBeforeRetryRequeueStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=1)
    agent = FakeAgent(fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )

    queued = await manager.run_now("task")
    did_work = await manager._execute_run(queued.run_id)
    latest = await manager.get_run(queued.run_id)
    attempts = await manager.list_attempts(queued.run_id)

    assert did_work is True
    assert latest.status == RunStatus.CANCELLED
    assert latest.cancel_requested_at is not None
    assert len(attempts) == 1
    assert attempts[-1].status == AttemptStatus.CANCELLED


@pytest.mark.asyncio
async def test_worker_loop_survives_transient_iteration_failure():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    original_recover = manager.recover_expired_runs
    failures_left = 1

    async def flaky_recover():
        nonlocal failures_left
        if failures_left:
            failures_left -= 1
            raise RuntimeError("transient store failure")
        await original_recover()

    manager.recover_expired_runs = flaky_recover
    await manager.start()
    try:
        queued = await manager.run_now("task")
        completed = await manager.wait_for_run(queued.run_id, timeout_seconds=2)

        assert completed.status == RunStatus.COMPLETED
        assert manager._worker_task is not None
        assert not manager._worker_task.done()
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_lifecycle_visibility_failure_does_not_block_execution():
    manager = BackgroundAgentManager(
        task_store="in_memory",
        workspace=BrokenWorkspace(),
    )
    agent = FakeAgent(response="complete")
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)
    latest = await manager.get_run(run.run_id)

    assert latest.status == RunStatus.COMPLETED
    assert agent.calls
    assert [event["event"] for event in manager._events[run.run_id]] == [
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_completed",
    ]


@pytest.mark.asyncio
async def test_heartbeat_visibility_failure_does_not_stop_lease_refresh():
    store = CountingStore()
    manager = BackgroundAgentManager(
        task_store=store,
        workspace=BrokenWorkspace(),
        lease_seconds=1,
    )
    agent = FakeAgent(response="complete", delay=0.35)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert store.refresh_count >= 1
    assert any(
        event["event"] == "background_run_heartbeat"
        for event in manager._events[run.run_id]
    )


@pytest.mark.asyncio
async def test_retry_delay_sets_future_queue_time_and_blocks_claim():
    manager = BackgroundAgentManager(task_store="in_memory")
    agent = FakeAgent(response="ok", fail_times=1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(
            max_retries=1,
            initial_delay_seconds=5,
            max_delay_seconds=30,
            backoff=BackoffPolicy.EXPONENTIAL,
        ),
    )

    run = await manager.run_now("task", wait=True)
    attempts = await manager.list_attempts(run.run_id)
    claimable = await manager.task_store.list_claimable_runs(limit=10)

    assert run.status == RunStatus.QUEUED
    assert attempts[0].retry_delay_seconds == 5
    assert all(item.run_id != run.run_id for item in claimable)


@pytest.mark.asyncio
async def test_long_running_attempt_refreshes_lease():
    store = CountingStore()
    manager = BackgroundAgentManager(task_store=store, lease_seconds=0.5)
    await manager.register_agent("agent", FakeAgent(response="ok", delay=0.7))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)

    assert run.status == RunStatus.COMPLETED
    assert store.refresh_count >= 1


@pytest.mark.asyncio
async def test_expired_lease_cannot_be_refreshed_by_stale_owner():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec())
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="work",
        trigger_type=TriggerType.MANUAL,
        session_id="session",
        workspace_path="workspace",
    )
    created = await store.create_run_with_overlap_guard(
        run, OverlapPolicy.ALLOW_PARALLEL
    )
    claimed = await store.claim_run(created.run_id, "worker", lease_seconds=-1)

    with pytest.raises(RunLeaseError):
        await store.refresh_lease(
            claimed.run_id,
            "worker",
            claimed.lease_token,
            lease_seconds=30,
        )


@pytest.mark.asyncio
async def test_running_cancel_finishes_cancelled_not_completed():
    manager = BackgroundAgentManager(task_store="in_memory", lease_seconds=1)
    agent = FakeAgent(response="late", delay=0.1)
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    await manager.start()
    run = await manager.run_now("task")
    await wait_for(lambda: agent.calls, timeout=0.5)
    await manager.cancel_run(run.run_id)
    async def terminal_run():
        latest = await manager.get_run(run.run_id)
        if latest and latest.status in {
            RunStatus.CANCELLED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.TIMEOUT,
        }:
            return latest
        return None

    terminal = await wait_for(terminal_run, timeout=1.0)
    await manager.shutdown()

    assert terminal.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_execute_one_releases_claim_when_cancelled_before_run_starts():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task")

    async def cancelled_before_start(claimed):
        raise asyncio.CancelledError

    manager._supervisor.run_claimed = cancelled_before_start

    with pytest.raises(asyncio.CancelledError):
        await manager._execute_one()

    released = await manager.get_run(run.run_id)
    assert released.status == RunStatus.QUEUED
    assert released.lease_owner is None
    assert released.lease_token is None
    assert [event["event"] for event in manager._events[run.run_id]] == [
        "background_run_queued",
        "background_run_queued",
    ]


@pytest.mark.asyncio
async def test_execute_one_release_claim_honors_cancelled_transition_race():
    store = CancellingBeforeClaimReleaseStore()
    manager = BackgroundAgentManager(task_store=store)
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task")

    async def cancelled_before_start(claimed):
        raise asyncio.CancelledError

    manager._supervisor.run_claimed = cancelled_before_start

    with pytest.raises(asyncio.CancelledError):
        await manager._execute_one()

    cancelled = await manager.get_run(run.run_id)
    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_recover_expired_running_run_requeues_when_retry_available():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store, worker_id="recovery")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )
    run = await manager.run_now("task", wait=False)
    claimed = await store.claim_run(run.run_id, "lost_worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        {"attempt": 1},
        "lost_worker",
        claimed.lease_token,
    )
    await store.create_attempt(
        BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="lost_worker",
            lease_token=running.lease_token,
        )
    )
    async with store._lock:
        store._runs[run.run_id] = running.model_copy(
            update={"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

    await manager.recover_expired_runs()
    recovered = await manager.get_run(run.run_id)

    assert recovered.status == RunStatus.QUEUED
    assert recovered.lease_token is None


@pytest.mark.asyncio
async def test_recovery_service_requeues_expired_running_run_directly():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(
        task_spec(retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0))
    )
    run = BackgroundRun(
        task_id="task",
        agent_id="agent",
        query_snapshot="one",
        trigger_type=TriggerType.MANUAL,
        session_id="s1",
        workspace_path="w1",
    )
    created = await store.create_run_with_overlap_guard(run, OverlapPolicy.ALLOW_PARALLEL)
    claimed = await store.claim_run(created.run_id, "lost_worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        {"attempt": 1},
        "lost_worker",
        claimed.lease_token,
    )
    await store.create_attempt(
        BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="lost_worker",
            lease_token=running.lease_token,
        )
    )
    async with store._lock:
        store._runs[run.run_id] = running.model_copy(
            update={"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

    emitted = []

    async def emit_run(event_name, run, **extra):
        emitted.append((event_name, run.status))

    transitions = BackgroundRunTransitions(
        task_store=store,
        worker_id=lambda: "recovery",
        lease_seconds=lambda: 30,
        emit_run=emit_run,
    )
    recovery = BackgroundRunRecovery(
        task_store=store,
        transitions=transitions,
        worker_id=lambda: "recovery",
        lease_seconds=lambda: 30,
        emit_run=emit_run,
    )

    await recovery.recover_expired_runs()

    recovered = await store.get_run(run.run_id)
    attempts = await store.list_attempts(run.run_id)
    assert recovered.status == RunStatus.QUEUED
    assert recovered.lease_token is None
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].reason == AttemptReason.LEASE_EXPIRED
    assert emitted == [("background_run_recovered", RunStatus.QUEUED)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [CancellingBeforeRecoveryRetryingStore, CancellingBeforeRecoveryRequeueStore],
)
async def test_recover_expired_running_run_honors_cancelled_transition_race(
    store_factory,
):
    store = store_factory()
    manager = BackgroundAgentManager(task_store=store, worker_id="recovery")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )
    run = await manager.run_now("task", wait=False)
    claimed = await store.claim_run(run.run_id, "lost_worker", lease_seconds=30)
    running = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        {"attempt": 1},
        "lost_worker",
        claimed.lease_token,
    )
    await store.create_attempt(
        BackgroundAttempt(
            run_id=running.run_id,
            attempt_number=1,
            worker_id="lost_worker",
            lease_token=running.lease_token,
        )
    )
    async with store._lock:
        store._runs[run.run_id] = running.model_copy(
            update={"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

    await manager.recover_expired_runs()
    recovered = await manager.get_run(run.run_id)

    assert recovered.status == RunStatus.CANCELLED
    assert recovered.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_expired_lease_during_completion_does_not_crash_worker():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(
        task_store=store,
        worker_id="worker",
        lease_seconds=0.05,
    )
    await manager.register_agent("agent", BlockingAgent(response="done", delay=0.1))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )
    run = await manager.run_now("task", wait=False)

    did_work = await manager._execute_one()
    latest = await manager.get_run(run.run_id)
    attempts = await manager.list_attempts(run.run_id)

    assert did_work is True
    assert latest.status in {RunStatus.CLAIMED, RunStatus.RUNNING}
    if attempts:
        assert attempts[0].status == AttemptStatus.RUNNING

    await manager.recover_expired_runs()
    still_recoverable = await manager.get_run(run.run_id)
    assert still_recoverable.status in {
        RunStatus.CLAIMED,
        RunStatus.RUNNING,
        RunStatus.RETRYING,
        RunStatus.QUEUED,
    }

    if still_recoverable.status != RunStatus.QUEUED:
        manager.lease_seconds = 1
        await manager.recover_expired_runs()
    recovered = await manager.get_run(run.run_id)
    recovered_attempts = await manager.list_attempts(run.run_id)

    assert recovered.status == RunStatus.QUEUED
    assert recovered.lease_token is None
    if attempts:
        assert recovered_attempts[0].status == AttemptStatus.FAILED
        assert recovered_attempts[0].reason == AttemptReason.LEASE_EXPIRED
    else:
        assert recovered_attempts == []


@pytest.mark.asyncio
async def test_recover_expired_retrying_run_requeues_without_self_transition():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store, worker_id="recovery")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=0),
    )
    run = await manager.run_now("task", wait=False)
    claimed = await store.claim_run(run.run_id, "lost_worker", lease_seconds=30)
    retrying = await store.transition_run(
        claimed.run_id,
        {RunStatus.CLAIMED},
        RunStatus.RUNNING,
        {"attempt": 1},
        "lost_worker",
        claimed.lease_token,
    )
    retrying = await store.transition_run(
        retrying.run_id,
        {RunStatus.RUNNING},
        RunStatus.RETRYING,
        {"error": "crashed during retry release"},
        "lost_worker",
        retrying.lease_token,
    )
    async with store._lock:
        store._runs[run.run_id] = retrying.model_copy(
            update={"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

    await manager.recover_expired_runs()
    recovered = await manager.get_run(run.run_id)

    assert recovered.status == RunStatus.QUEUED
    assert recovered.lease_token is None


@pytest.mark.asyncio
async def test_update_task_schedule_revisions_and_recomputes_due_time():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec(schedule={"type": "interval", "seconds": 60}))
    first = await store.get_schedule_state("task")

    await store.save_task(task_spec(schedule={"type": "interval", "seconds": 120}))
    second = await store.get_schedule_state("task")

    assert second.schedule_revision == first.schedule_revision + 1
    assert second.next_due_at != first.next_due_at


@pytest.mark.asyncio
async def test_delete_task_can_delete_historical_runs():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)

    await manager.delete_task("task", delete_runs=True)

    assert await manager.get_run(run.run_id) is None


@pytest.mark.asyncio
async def test_sql_task_store_persists_runs_across_manager_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    manager = BackgroundAgentManager(task_store={"backend": "sql", "url": url})
    assert isinstance(manager.task_store, SqlTaskStore)
    await manager.register_agent("agent", FakeAgent(response="persisted"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)
    await manager.shutdown()

    restored = BackgroundAgentManager(task_store={"backend": "sql", "url": url})
    await restored.task_store.initialize()

    assert (await restored.get_run(run.run_id)).status == RunStatus.COMPLETED
    assert (await restored.get_task("task")).task_id == "task"
    await restored.task_store.close()


@pytest.mark.asyncio
async def test_sql_task_store_does_not_wipe_state_when_used_before_start(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    first = BackgroundAgentManager(task_store={"backend": "sql", "url": url})
    await first.register_agent("agent", FakeAgent(response="persisted"))
    await first.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await first.run_now("task", wait=True)

    second = BackgroundAgentManager(task_store={"backend": "sql", "url": url})
    await second.register_agent("new_agent", FakeAgent(), replace=True)

    assert (await second.get_run(run.run_id)).status == RunStatus.COMPLETED
    assert (await second.get_task("task")).task_id == "task"


@pytest.mark.asyncio
async def test_sql_task_store_claim_is_atomic_across_managers(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    first = BackgroundAgentManager(task_store={"backend": "sql", "url": url})
    await first.register_agent("agent", FakeAgent())
    await first.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await first.run_now("task")

    store_a = SqlTaskStore(url=url)
    store_b = SqlTaskStore(url=url)
    claimed_a = await store_a.claim_next_run("worker_a", lease_seconds=30)
    claimed_b = await store_b.claim_next_run("worker_b", lease_seconds=30)

    assert claimed_a.run_id == run.run_id
    assert claimed_b is None


@pytest.mark.asyncio
async def test_set_schedule_paused_preserves_latest_schedule_cursor():
    store = InMemoryTaskStore()
    await store.save_agent(agent_spec())
    await store.save_task(task_spec(schedule={"type": "interval", "seconds": 60}))
    state = await store.get_schedule_state("task")
    next_due_at = state.next_due_at + timedelta(minutes=5)
    occurrence_id = build_occurrence_id(
        ScheduleType.INTERVAL, state.schedule_revision, state.next_due_at
    )

    await store.advance_schedule(
        "task",
        expected_revision=state.schedule_revision,
        occurrence_id=occurrence_id,
        next_due_at=next_due_at,
    )
    paused = await store.set_schedule_paused("task", True)

    assert paused.paused is True
    assert paused.next_due_at == next_due_at
    assert paused.last_due_at == state.next_due_at


def test_task_store_router_builds_redis_and_mongodb_stores(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE", "env_tasks")

    redis_store = TaskStoreRouter.create("redis")
    mongo_store = TaskStoreRouter.create("mongodb")

    assert isinstance(redis_store, RedisTaskStore)
    assert redis_store.url == "redis://localhost:6379/1"
    assert isinstance(mongo_store, MongoDbTaskStore)
    assert mongo_store.uri == "mongodb://localhost:27017"
    assert mongo_store.database_name == "env_tasks"


def test_task_store_router_accepts_explicit_redis_and_mongodb_config():
    redis_store = TaskStoreRouter.create(
        {
            "backend": "redis",
            "url": "redis://localhost:6379/2",
            "prefix": "custom:background",
            "connect_timeout": 1.5,
        }
    )
    mongo_store = TaskStoreRouter.create(
        {
            "backend": "mongodb",
            "uri": "mongodb://localhost:27017",
            "database": "tasks",
            "collection_prefix": "custom_tasks",
            "connect_timeout": 2.0,
        }
    )

    assert isinstance(redis_store, RedisTaskStore)
    assert redis_store.prefix == "custom:background"
    assert redis_store.connect_timeout == 1.5
    assert isinstance(mongo_store, MongoDbTaskStore)
    assert mongo_store.database_name == "tasks"
    assert mongo_store.collection_prefix == "custom_tasks"
    assert mongo_store.connect_timeout == 2.0


def test_task_store_router_uses_common_prefix_for_mongodb_collections():
    mongo_store = TaskStoreRouter.create(
        {
            "backend": "mongodb",
            "uri": "mongodb://localhost:27017",
            "database": "tasks",
            "prefix": "common_tasks",
        }
    )

    assert isinstance(mongo_store, MongoDbTaskStore)
    assert mongo_store.collection_prefix == "common_tasks"


@pytest.mark.asyncio
async def test_remote_task_stores_lazy_initialize_before_manager_registration():
    redis_store = FakeRedisTaskStore(FakeRedisClient())
    redis_manager = BackgroundAgentManager(task_store=redis_store)

    await redis_manager.register_agent("agent", FakeAgent())

    assert redis_store.initialize_count == 1
    assert (await redis_manager.get_agent("agent")).agent_id == "agent"

    mongo_store = FakeMongoTaskStore(FakeMongoDb())
    mongo_manager = BackgroundAgentManager(task_store=mongo_store)

    await mongo_manager.register_agent("agent", FakeAgent())

    assert mongo_store.initialize_count == 1
    assert (await mongo_manager.get_agent("agent")).agent_id == "agent"


@pytest.mark.asyncio
async def test_manager_shutdown_closes_lazy_initialized_task_store():
    store = FakeRedisTaskStore(FakeRedisClient())
    manager = BackgroundAgentManager(task_store=store)

    await manager.register_agent("agent", FakeAgent())
    await manager.shutdown()

    assert store.close_count == 1
    assert store._client is None


@pytest.mark.asyncio
async def test_redis_task_store_persists_state_through_backend_snapshot():
    client = FakeRedisClient()
    first = RedisTaskStore(url="redis://localhost:6379", prefix="test")
    first._client = client
    await first.save_agent(agent_spec())
    await first.save_task(task_spec())
    run = await first.create_run_with_overlap_guard(
        BackgroundRun(
            task_id="task",
            agent_id="agent",
            query_snapshot="do work",
            trigger_type=TriggerType.MANUAL,
            session_id="background:agent:task",
            workspace_path="background/agent/task/run",
        ),
        OverlapPolicy.SKIP_IF_RUNNING,
    )

    restored = RedisTaskStore(url="redis://localhost:6379", prefix="test")
    restored._client = client

    assert (await restored.get_task("task")).task_id == "task"
    assert (await restored.get_run(run.run_id)).query_snapshot == "do work"


@pytest.mark.asyncio
async def test_redis_task_store_cleans_previous_generation_after_commit():
    client = FakeRedisClient()
    store = RedisTaskStore(url="redis://localhost:6379", prefix="test")
    store._client = client

    await store.save_agent(agent_spec())
    first_generation = client.values[store._active_generation_key]
    await store.save_task(task_spec())
    second_generation = client.values[store._active_generation_key]

    assert second_generation != first_generation
    assert first_generation in client.values[store._previous_generation_key]

    await store.save_task(task_spec(task_id="task_2"))

    assert all(first_generation not in key for key in client.hashes)
    assert all(first_generation not in key for key in client.sets)


@pytest.mark.asyncio
async def test_redis_task_store_reads_use_backend_lock():
    client = FakeRedisClient()
    store = RedisTaskStore(
        url="redis://localhost:6379", prefix="test", lock_timeout=0.01
    )
    store._client = client
    client.values[store._lock_key] = "other-worker"

    with pytest.raises(TaskStoreError, match="Timed out acquiring Redis"):
        await store.get_agent("agent")


@pytest.mark.asyncio
async def test_redis_task_store_rejects_commit_after_lock_loss():
    client = FakeRedisClient()
    store = RedisTaskStore(url="redis://localhost:6379", prefix="test")
    store._client = client

    token = await store._acquire_lock()
    client.values.pop(store._lock_key)

    with pytest.raises(Exception, match="lock expired before commit"):
        await store._persist_backend_state(token)


@pytest.mark.asyncio
async def test_mongodb_task_store_persists_state_through_backend_snapshot():
    db = FakeMongoDb()
    first = MongoDbTaskStore(uri="mongodb://localhost:27017", database="test")
    first._db = db
    await first._lock_collection.update_one(
        {"_id": "task_store"},
        {"$setOnInsert": {"token": None, "expires_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    await first.save_agent(agent_spec())
    await first.save_task(task_spec())
    run = await first.create_run_with_overlap_guard(
        BackgroundRun(
            task_id="task",
            agent_id="agent",
            query_snapshot="do work",
            trigger_type=TriggerType.MANUAL,
            session_id="background:agent:task",
            workspace_path="background/agent/task/run",
        ),
        OverlapPolicy.SKIP_IF_RUNNING,
    )

    restored = MongoDbTaskStore(uri="mongodb://localhost:27017", database="test")
    restored._db = db

    assert (await restored.get_task("task")).task_id == "task"
    assert (await restored.get_run(run.run_id)).query_snapshot == "do work"


@pytest.mark.asyncio
async def test_mongodb_task_store_cleans_previous_generation_after_commit():
    db = FakeMongoDb()
    store = MongoDbTaskStore(uri="mongodb://localhost:27017", database="test")
    store._db = db
    await store._lock_collection.update_one(
        {"_id": "task_store"},
        {"$setOnInsert": {"token": None, "expires_at": datetime.now(timezone.utc)}},
        upsert=True,
    )

    await store.save_agent(agent_spec())
    first_generation = db["omnicoreagent_background_locks"].docs["task_store"][
        "active_generation"
    ]
    await store.save_task(task_spec())
    second_generation = db["omnicoreagent_background_locks"].docs["task_store"][
        "active_generation"
    ]

    assert second_generation != first_generation
    assert (
        db["omnicoreagent_background_locks"].docs["task_store"]["previous_generation"]
        == first_generation
    )

    await store.save_task(task_spec(task_id="task_2"))

    for collection in db.collections.values():
        assert all(
            doc.get("_generation") != first_generation
            for doc in collection.docs.values()
        )


@pytest.mark.asyncio
async def test_mongodb_task_store_reads_use_backend_lock():
    db = FakeMongoDb()
    store = MongoDbTaskStore(
        uri="mongodb://localhost:27017", database="test", lock_timeout=0.01
    )
    store._db = db
    await store._lock_collection.update_one(
        {"_id": "task_store"},
        {
            "$setOnInsert": {
                "token": "other-worker",
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=60),
            }
        },
        upsert=True,
    )

    with pytest.raises(TaskStoreError, match="Timed out acquiring MongoDB"):
        await store.get_agent("agent")


@pytest.mark.asyncio
async def test_mongodb_task_store_rejects_commit_after_lock_loss():
    db = FakeMongoDb()
    store = MongoDbTaskStore(uri="mongodb://localhost:27017", database="test")
    store._db = db
    await store._lock_collection.update_one(
        {"_id": "task_store"},
        {"$setOnInsert": {"token": None, "expires_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    token = await store._acquire_lock()
    await store._lock_collection.update_one(
        {"_id": "task_store", "token": token},
        {"$set": {"token": None, "expires_at": datetime.now(timezone.utc)}},
    )

    with pytest.raises(Exception, match="lock expired before commit"):
        await store._persist_backend_state(token)


@pytest.mark.asyncio
async def test_cancel_delayed_retry_marks_run_cancelled_immediately():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent(fail_times=1))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        retry_policy=RetryPolicy(max_retries=1, initial_delay_seconds=60),
    )
    run = await manager.run_now("task", wait=True)

    await manager.cancel_run(run.run_id)
    cancelled = await manager.get_run(run.run_id)

    assert cancelled.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_claimed_run_owned_by_manager_marks_terminal():
    manager = BackgroundAgentManager(task_store="in_memory", worker_id="owner")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task")
    await manager.task_store.claim_run(run.run_id, "owner", lease_seconds=30)

    await manager.cancel_run(run.run_id)

    cancelled = await manager.get_run(run.run_id)
    assert cancelled.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_claimed_run_owned_by_other_worker_records_intent_only():
    manager = BackgroundAgentManager(task_store="in_memory", worker_id="requester")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task")
    await manager.task_store.claim_run(run.run_id, "other_worker", lease_seconds=30)

    await manager.cancel_run(run.run_id)

    latest = await manager.get_run(run.run_id)
    assert latest.status == RunStatus.CLAIMED
    assert latest.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_background_run_writes_workspace_lifecycle_files(tmp_path):
    workspace = Workspace.from_config(workspace_dir=tmp_path).ensure()
    manager = BackgroundAgentManager(task_store="in_memory", workspace=workspace)
    await manager.register_agent("agent", FakeAgent(response="workspace"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )

    run = await manager.run_now("task", wait=True)
    run_json = tmp_path / "files" / run.workspace_path / "run.json"
    events_jsonl = tmp_path / "files" / run.workspace_path / "events.jsonl"

    assert json.loads(run_json.read_text())["run_id"] == run.run_id
    events = [json.loads(line) for line in events_jsonl.read_text().splitlines()]
    assert events[0]["event"] == "background_run_queued"
    assert events[-1]["event"] == "background_run_completed"


@pytest.mark.asyncio
async def test_background_run_events_replay_from_workspace_after_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'background.db'}"
    workspace = Workspace.from_config(workspace_dir=tmp_path / "workspace").ensure()
    manager = BackgroundAgentManager(
        task_store={"backend": "sql", "url": url}, workspace=workspace
    )
    await manager.register_agent("agent", FakeAgent(response="workspace"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)

    restored = BackgroundAgentManager(
        task_store={"backend": "sql", "url": url}, workspace=workspace
    )
    events = await restored.get_run_events(run.run_id)

    assert events[0]["event"] == "background_run_queued"
    assert events[-1]["event"] == "background_run_completed"


@pytest.mark.asyncio
async def test_workspace_policy_can_disable_lifecycle_files(tmp_path):
    workspace = Workspace.from_config(workspace_dir=tmp_path).ensure()
    manager = BackgroundAgentManager(task_store="in_memory", workspace=workspace)
    await manager.register_agent("agent", FakeAgent(response="workspace"))
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "manual"},
        workspace_policy={"write_run_json": False, "write_events_jsonl": False},
    )

    run = await manager.run_now("task", wait=True)

    assert not (tmp_path / "files" / run.workspace_path / "run.json").exists()
    assert not (tmp_path / "files" / run.workspace_path / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_skip_missed_interval_advances_from_now_not_stale_due():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "interval", "seconds": 60, "misfire_policy": "skip_missed"},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )
    stale_due = datetime.now(timezone.utc) - timedelta(hours=1)
    state = await manager.task_store.get_schedule_state("task")
    await manager.task_store.save_schedule_state(
        state.model_copy(update={"next_due_at": stale_due})
    )

    await manager._dispatch_due_schedules()
    updated = await manager.task_store.get_schedule_state("task")

    assert updated.next_due_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_run_once_misfire_dispatches_one_run_and_advances_from_now():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "interval", "seconds": 60, "misfire_policy": "run_once"},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )
    stale_due = datetime.now(timezone.utc) - timedelta(hours=1)
    state = await manager.task_store.get_schedule_state("task")
    await manager.task_store.save_schedule_state(
        state.model_copy(update={"next_due_at": stale_due})
    )

    await manager._dispatch_due_schedules()

    runs = await manager.list_runs(task_id="task")
    updated = await manager.task_store.get_schedule_state("task")
    assert len(runs) == 1
    assert runs[0].due_at == stale_due
    assert updated.next_due_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_queue_all_misfire_dispatches_due_occurrences_until_limit():
    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent("agent", FakeAgent())
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="do work",
        schedule={"type": "interval", "seconds": 60, "misfire_policy": "queue_all"},
        overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
    )
    now = datetime.now(timezone.utc)
    first_due = now - timedelta(minutes=5)
    state = await manager.task_store.get_schedule_state("task")
    await manager.task_store.save_schedule_state(
        state.model_copy(update={"next_due_at": first_due})
    )

    await manager._dispatch_due_schedules(limit=3)

    runs = await manager.list_runs(task_id="task")
    updated = await manager.task_store.get_schedule_state("task")
    assert [run.due_at for run in runs] == [
        first_due,
        first_due + timedelta(seconds=60),
        first_due + timedelta(seconds=120),
    ]
    assert updated.next_due_at == first_due + timedelta(seconds=180)
    assert updated.next_due_at < datetime.now(timezone.utc)

    await manager._dispatch_due_schedules(limit=10)

    runs = await manager.list_runs(task_id="task")
    assert len(runs) >= 6
    assert len({run.occurrence_id for run in runs}) == len(runs)
    assert (await manager.task_store.get_schedule_state("task")).next_due_at > (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )


def test_schedule_jitter_is_bounded_and_stable():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    schedule = ScheduleSpec(
        type="interval",
        seconds=60,
        jitter_seconds=30,
        misfire_policy=MisfirePolicy.RUN_ONCE,
    )

    initial_due = initial_schedule_due(schedule, base)
    initial_offset = deterministic_jitter_seconds(
        schedule, base + timedelta(seconds=60)
    )
    assert initial_due == base + timedelta(seconds=60 + initial_offset)
    assert 0 <= initial_offset <= 30
    assert initial_due == initial_schedule_due(schedule, base)

    next_due = next_schedule_due(schedule, initial_due, initial_due)
    next_offset = deterministic_jitter_seconds(
        schedule, initial_due + timedelta(seconds=60)
    )
    assert next_due == initial_due + timedelta(seconds=60 + next_offset)


def test_schedule_jitter_respects_end_at_boundary():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    for seconds in range(1, 120):
        schedule = ScheduleSpec(
            type="interval",
            seconds=seconds,
            jitter_seconds=30,
        )
        raw_due = base + timedelta(seconds=seconds)
        offset = deterministic_jitter_seconds(schedule, raw_due)
        if offset <= 0:
            continue
        bounded = ScheduleSpec(
            type="interval",
            seconds=seconds,
            jitter_seconds=30,
            end_at=raw_due + timedelta(seconds=offset - 1),
        )
        assert initial_schedule_due(bounded, base) is None
        assert next_schedule_due(bounded, base, base) is None
        return
    raise AssertionError("expected at least one deterministic jitter offset > 0")


def test_cron_due_uses_configured_timezone():
    after = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    due = next_cron_due("0 9 * * *", after, "Africa/Lagos")

    assert due.hour == 8
    assert due.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_register_agent_persists_mcp_and_workspace_config():
    manager = BackgroundAgentManager(task_store="in_memory")

    spec = await manager.register_agent("agent", ConfiguredFakeAgent())

    assert spec.mcp_tools == [{"name": "filesystem", "transport": "stdio"}]
    assert spec.workspace_config == {
        "workspace_backend": "local",
        "workspace_dir": "custom",
    }


def test_occurrence_id_scope_includes_task_revision_and_due_time():
    due_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    occurrence = build_occurrence_id(ScheduleType.ONCE, 3, due_at)
    assert occurrence == "once:3:2026-01-01T00:00:00+00:00"
