"""Scale plan S2: several workers on one SQL database.

The SQL task store used to be one process's store: it kept all of its state
in memory and rewrote it under the SQLite file's write lock. These are the
properties that make it a shared one — two stores on one database see the same
runs, a run under contention is claimed exactly once, and a deployment that
upgrades keeps the state the snapshot store wrote.

Each runs on SQLite, which every checkout has, and on PostgreSQL when
``OMNICOREAGENT_TEST_POSTGRES_URL`` is set (CI sets it), where the claim takes
real row locks.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from omnicoreagent.background import (
    BackgroundAgentSpec,
    OverlapPolicy,
    RunStatus,
    SqlTaskStore,
)

from test_background_task_store_contract import background_run, task_spec

POSTGRES_URL_ENV = "OMNICOREAGENT_TEST_POSTGRES_URL"


@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path):
    """Where the stores meet: a file, or a PostgreSQL database."""
    if request.param == "sqlite":
        yield {"url": f"sqlite:///{tmp_path / 'background.db'}", "table_prefix": ""}
        return
    url = os.getenv(POSTGRES_URL_ENV)
    if not url:
        pytest.skip(f"PostgreSQL shared task store tests need {POSTGRES_URL_ENV}")
    prefix = f"t{uuid4().hex[:12]}_"
    yield {"url": url, "table_prefix": prefix}
    cleaner = SqlTaskStore(url, table_prefix=prefix)
    asyncio.run(_drop(cleaner))


async def _drop(store: SqlTaskStore) -> None:
    await store.initialize()
    try:
        await asyncio.to_thread(store._tables.metadata.drop_all, store._engine)
    finally:
        await store.close()


async def _store(database: dict) -> SqlTaskStore:
    store = SqlTaskStore(database["url"], table_prefix=database["table_prefix"])
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_two_stores_on_one_database_see_the_same_runs(database):
    writer = await _store(database)
    reader = await _store(database)
    try:
        await writer.save_agent(BackgroundAgentSpec(agent_id="agent"))
        await writer.save_task(task_spec(overlap_policy=OverlapPolicy.ALLOW_PARALLEL))
        run = await writer.create_run_with_overlap_guard(
            background_run(), OverlapPolicy.ALLOW_PARALLEL
        )

        assert (await reader.get_run(run.run_id)).run_id == run.run_id
        assert [task.task_id for task in await reader.list_tasks()] == ["task"]

        # The second store claims it, and the first sees the lease.
        claimed = await reader.claim_next_run("worker-b", lease_seconds=30)
        assert claimed.run_id == run.run_id
        seen = await writer.get_run(run.run_id)
        assert seen.status == RunStatus.CLAIMED
        assert seen.lease_owner == "worker-b"
        # And the first store cannot transition it without the lease.
        with pytest.raises(Exception):
            await writer.transition_run(
                run.run_id, {RunStatus.CLAIMED}, RunStatus.RUNNING, None, "worker-a", "wrong"
            )
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.asyncio
async def test_a_contended_run_is_claimed_once(database):
    """Six workers, six runs, all claiming at once: no run twice, none lost."""
    setup = await _store(database)
    await setup.save_agent(BackgroundAgentSpec(agent_id="agent"))
    await setup.save_task(task_spec(overlap_policy=OverlapPolicy.ALLOW_PARALLEL))
    for _ in range(6):
        await setup.create_run_with_overlap_guard(
            background_run(), OverlapPolicy.ALLOW_PARALLEL
        )
    await setup.close()

    workers = [await _store(database) for _ in range(6)]
    try:
        claims = await asyncio.gather(
            *(
                worker.claim_next_run(f"worker-{number}", lease_seconds=30)
                for number, worker in enumerate(workers)
            ),
            return_exceptions=True,
        )
        failures = [claim for claim in claims if isinstance(claim, BaseException)]
        assert not failures, failures
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 6, "a claim came back empty while runs were queued"
        assert len({claim.run_id for claim in claimed}) == 6, "a run was claimed twice"
        assert len({claim.lease_token for claim in claimed}) == 6

        stored = await workers[0].list_runs()
        assert {run.status for run in stored} == {RunStatus.CLAIMED}
        assert {run.lease_owner for run in stored} == {
            f"worker-{number}" for number in range(6)
        }
    finally:
        for worker in workers:
            await worker.close()


@pytest.mark.asyncio
async def test_state_the_snapshot_store_wrote_is_taken_over(database):
    """A deployment upgrading from the snapshot store keeps its runs."""
    from sqlalchemy import create_engine, text

    keeper = await _store(database)
    await keeper.save_agent(BackgroundAgentSpec(agent_id="agent"))
    await keeper.save_task(task_spec(overlap_policy=OverlapPolicy.ALLOW_PARALLEL))
    spec = await keeper.get_task("task")
    agent = await keeper.get_agent("agent")
    run = background_run()
    # Put the old store's table beside the new ones, and empty the new ones.
    await asyncio.to_thread(keeper._tables.metadata.drop_all, keeper._engine)
    await keeper.close()

    prefix = database["table_prefix"]
    engine = create_engine(database["url"], future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE {prefix}background_state "
                    "(kind VARCHAR(32) NOT NULL, id VARCHAR(255) NOT NULL, "
                    "data TEXT NOT NULL, PRIMARY KEY (kind, id))"
                )
            )
            for kind, record_id, data in (
                ("agent", "agent", agent.model_dump_json()),
                ("task", "task", spec.model_dump_json()),
                ("run", run.run_id, run.model_dump_json()),
                ("cancel", run.run_id, "{}"),
            ):
                connection.execute(
                    text(
                        f"INSERT INTO {prefix}background_state (kind, id, data) "
                        "VALUES (:kind, :id, :data)"
                    ),
                    {"kind": kind, "id": record_id, "data": data},
                )
    finally:
        engine.dispose()

    upgraded = await _store(database)
    try:
        assert (await upgraded.get_agent("agent")).agent_id == "agent"
        assert (await upgraded.get_task("task")).task_id == "task"
        taken = await upgraded.get_run(run.run_id)
        assert taken is not None and taken.run_id == run.run_id
        assert await upgraded.is_cancel_requested(run.run_id) is True

        # Importing happens once: a second store finds the rows already there
        # and leaves them alone.
        again = await _store(database)
        try:
            assert len(await again.list_runs()) == 1
        finally:
            await again.close()
    finally:
        with_prefix_engine = create_engine(database["url"], future=True)
        try:
            with with_prefix_engine.begin() as connection:
                connection.execute(text(f"DROP TABLE {prefix}background_state"))
        finally:
            with_prefix_engine.dispose()
        await upgraded.close()
