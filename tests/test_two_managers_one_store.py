"""Scale plan S4: two worker processes, one SQL store, no work lost or doubled.

S2 made a task store several processes can share; this is what sharing it has
to mean. Two managers with their own worker identities run against one
database:

- queued runs are divided between them, and each run runs exactly once;
- both managers really do take work — the agent here will not finish until two
  runs are in flight at once, so a test where only one manager ever claims
  cannot pass by being fast;
- a manager that stops holding a claim does not strand it: its lease expires
  and the other manager takes the run over and finishes it.

On SQLite, which every checkout has, on PostgreSQL when
``OMNICOREAGENT_TEST_POSTGRES_URL`` is set (CI sets it), where claims take real
row locks, and on Redis, whose claims are optimistic transactions on the run's
own key (scale plan S2b).
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from omnicoreagent.background import BackgroundAgentManager, RunStatus
from omnicoreagent.background.store.redis import RedisTaskStore
from omnicoreagent.background.store.sql import SqlTaskStore
from omnicoreagent.core.workspace.manager import Workspace

POSTGRES_URL_ENV = "OMNICOREAGENT_TEST_POSTGRES_URL"
REDIS_URL_ENV = "OMNICOREAGENT_TEST_REDIS_URL"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(params=["sqlite", "postgres", "redis"])
def store_config(request, tmp_path):
    """One database, described the way a deployment describes it."""
    if request.param == "sqlite":
        yield {"backend": "sql", "url": f"sqlite:///{tmp_path / 'background.db'}"}
        return
    if request.param == "redis":
        redis_url = os.getenv(REDIS_URL_ENV, DEFAULT_REDIS_URL)
        prefix = f"test:two-managers:{uuid4().hex}"
        config = {
            "backend": "redis",
            "url": redis_url,
            "prefix": prefix,
            "connect_timeout": 2.0,
        }
        try:
            asyncio.run(_reachable(config))
        except Exception as exc:
            pytest.skip(f"Redis task store unavailable: {exc}")
        yield config
        asyncio.run(_clear_redis(redis_url, prefix))
        return
    url = os.getenv(POSTGRES_URL_ENV)
    if not url:
        pytest.skip(f"PostgreSQL two-manager tests need {POSTGRES_URL_ENV}")
    prefix = f"t{uuid4().hex[:12]}_"
    yield {"backend": "sql", "url": url, "prefix": prefix}
    cleaner = SqlTaskStore(url, table_prefix=prefix)
    asyncio.run(_drop(cleaner))


async def _reachable(config: dict) -> None:
    store = RedisTaskStore(
        url=config["url"], prefix=config["prefix"], connect_timeout=2.0
    )
    try:
        await store.initialize()
    finally:
        await store.close()


async def _clear_redis(url: str, prefix: str) -> None:
    """The test's keys go with it, so a shared server stays clean."""
    store = RedisTaskStore(url=url, prefix=prefix, connect_timeout=2.0)
    try:
        await store.initialize()
        client = store._require_client()
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*", count=500)]
        if keys:
            await client.delete(*keys)
    except Exception:
        pass
    finally:
        await store.close()


async def _drop(store: SqlTaskStore) -> None:
    await store.initialize()
    try:
        await asyncio.to_thread(store._tables.metadata.drop_all, store._engine)
    finally:
        await store.close()


class TogetherAgent:
    """Records which worker ran what, and holds until two runs are in flight.

    A manager cannot satisfy this alone: the first run waits for a second to
    start beside it, so the test only passes when both managers claim.
    """

    def __init__(self, ran: dict[str, list[str]], together: asyncio.Event, wanted: int):
        self.ran = ran
        self.together = together
        self.wanted = wanted
        self.in_flight = 0

    async def run(self, query: str, session_id: str | None = None):
        self.in_flight += 1
        if self.in_flight >= self.wanted:
            self.together.set()
        try:
            await asyncio.wait_for(self.together.wait(), timeout=30)
        finally:
            self.in_flight -= 1
        self.ran.setdefault(query, []).append("ran")
        return {"response": f"done: {query}"}


async def _manager(store_config: dict, workspace, worker_id: str, agent, **kwargs):
    manager = BackgroundAgentManager(
        task_store=store_config,
        workspace=workspace,
        worker_id=worker_id,
        **kwargs,
    )
    await manager.initialize()
    await manager.register_agent("agent", agent, replace=True)
    return manager


@pytest.mark.asyncio
async def test_two_managers_divide_the_runs_and_run_each_once(store_config, tmp_path):
    workspace = Workspace.from_config(workspace_dir=tmp_path / "workspace").ensure()
    ran: dict[str, list[str]] = {}
    together = asyncio.Event()
    first = await _manager(
        store_config, workspace, "worker-a", TogetherAgent(ran, together, 2), lease_seconds=120
    )
    second = await _manager(
        store_config, workspace, "worker-b", TogetherAgent(ran, together, 2), lease_seconds=120
    )
    try:
        await first.register_task(
            task_id="task",
            agent_id="agent",
            query="shared work",
            schedule={"type": "manual"},
            overlap_policy="allow_parallel",
        )
        queued = [(await first.run_now("task")).run_id for _ in range(4)]

        await first.start()
        await second.start()
        finished = await asyncio.gather(
            *(first.run_until_terminal(run_id, timeout_seconds=60) for run_id in queued)
        )

        assert [run.status for run in finished] == [RunStatus.COMPLETED] * 4
        # Each run ran once: the agent recorded one entry per run's query, and
        # a run claimed twice would have recorded two.
        attempts = [len(await first.list_attempts(run_id)) for run_id in queued]
        assert attempts == [1, 1, 1, 1], attempts
        assert len({run.run_id for run in finished}) == 4
        # Both workers did work: the agent only finishes when two runs overlap.
        assert together.is_set()
        # One execution per run, and four distinct runs executed: a run
        # claimed twice would show two executions under its own prompt.
        assert len(ran) == 4, list(ran)
        assert all(len(times) == 1 for times in ran.values()), ran
    finally:
        await first.shutdown()
        await second.shutdown()


@pytest.mark.asyncio
async def test_a_stranded_claim_is_taken_over_by_the_other_manager(store_config, tmp_path):
    """A manager that stops mid-claim does not keep the run to itself."""
    workspace = Workspace.from_config(workspace_dir=tmp_path / "workspace").ensure()
    ran: dict[str, list[str]] = {}
    done = asyncio.Event()
    done.set()  # this agent does not need company
    first = await _manager(
        store_config, workspace, "worker-a", TogetherAgent(ran, done, 1), lease_seconds=1
    )
    second = await _manager(
        store_config, workspace, "worker-b", TogetherAgent(ran, done, 1), lease_seconds=60
    )
    try:
        await first.register_task(
            task_id="task",
            agent_id="agent",
            query="stranded work",
            schedule={"type": "manual"},
            retry_policy={"max_retries": 2, "initial_delay_seconds": 0},
        )
        run = await first.run_now("task")

        # The first manager claims it and then stops without finishing: the
        # claim is left behind with a lease of one second.
        claimed = await first.task_store.claim_run(run.run_id, "worker-a", lease_seconds=1)
        assert claimed.lease_owner == "worker-a"
        await first.shutdown()

        await asyncio.sleep(1.2)
        await second.start()
        recovered = await second.run_until_terminal(run.run_id, timeout_seconds=60)

        assert recovered.status == RunStatus.COMPLETED
        assert recovered.lease_owner is None
        assert ran, "the run never executed after being taken over"
    finally:
        await second.shutdown()
