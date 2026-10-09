"""SQLite takes concurrent writers, and an in-memory SQLite URL works.

Found by the P6 throughput run (2026-10-07): at 50 concurrent run-state saves
on a file database SQLite raised "database is locked", before and after the
tracks. Its default journal allows one writer and no readers, and its default
wait for a lock is nothing at all. File databases now run in WAL mode with a
busy timeout. Separately, ``DatabaseMessageStore("sqlite://")`` failed because
the pool arguments the store passes are invalid for SQLite's in-memory pool.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")

from omnicoreagent.core.memory_store import sql_db_memory
from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore


@pytest.fixture(autouse=True)
def _close_pools():
    yield
    sql_db_memory.close_all_sql_managers()


@pytest.mark.asyncio
async def test_fifty_concurrent_runs_saving_on_a_file_database_succeed(tmp_path):
    """Fifty runs, each saving its state and its messages and reading it back,
    as a server under load does."""
    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'runs.db'}")

    async def one_run(i):
        version = await store.save_run_state(
            {"run_id": f"r{i}", "session_id": "s", "status": "running"}, None
        )
        for _ in range(5):
            version = await store.save_run_state(
                {"run_id": f"r{i}", "session_id": "s", "status": "running"}, version
            )
            await store.store_message("user", "m", {"agent_name": "a"}, "s")
            await store.get_run_state(f"r{i}")
        return version

    versions = await asyncio.gather(*(one_run(i) for i in range(50)))

    assert versions == [6] * 50
    assert len(await store.list_run_states(limit=100)) == 50
    assert len(await store.get_messages("s", "a")) == 250


@pytest.mark.asyncio
async def test_a_file_database_runs_in_wal_mode_with_a_busy_timeout(tmp_path):
    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'wal.db'}")

    for engine in (store._sql_manager.get_engine(), store._sql_manager._read_engine):
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 5000


@pytest.mark.asyncio
async def test_an_in_memory_sqlite_url_works():
    store = DatabaseMessageStore("sqlite://")

    await store.store_message("user", "hi", {"agent_name": "a"}, "s1")
    version = await store.save_run_state({"run_id": "r", "session_id": "s1", "status": "running"}, None)

    assert [m["content"] for m in await store.get_messages("s1", "a")] == ["hi"]
    assert version == 1
    assert (await store.get_run_state("r"))["run_id"] == "r"
    # The read pool and the write pool see the same database.
    assert len(await store.list_run_states()) == 1


@pytest.mark.asyncio
async def test_in_memory_sqlite_takes_concurrent_budget_changes():
    store = DatabaseMessageStore("sqlite://")

    await asyncio.gather(
        *(store.apply_budget_change("k", {"guard": [("cost", 1.0, 100.0)]}) for _ in range(30))
    )

    assert (await store.get_budget_state("k"))["meters"]["cost"] == 30.0


@pytest.mark.asyncio
async def test_in_memory_sqlite_takes_concurrent_saves():
    store = DatabaseMessageStore("sqlite://")

    await asyncio.gather(
        *(
            store.save_run_state({"run_id": f"r{i}", "session_id": "s", "status": "running"}, None)
            for i in range(20)
        )
    )

    assert len(await store.list_run_states(limit=100)) == 20
