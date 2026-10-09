"""The SQL memory store does not ping on every checkout, and survives a drop.

The support desk ramp (2026-10-07): a py-spy profile at 30 users put about 45%
of samples in the SQL store's per-operation work. `pool_pre_ping=True` sent a
`SELECT 1` on every checkout (16% alone), every read ended in a rollback, and
the pool held a fixed 20+30 connections whatever the thread count. Pre-ping
existed to survive a connection the server had dropped; that is now done by
retrying the operation once on a fresh connection, which costs nothing on the
healthy path.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import event
from sqlalchemy.orm import Session

from omnicoreagent.core.memory_store import sql_db_memory
from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'memory.db'}"
    store = DatabaseMessageStore(db_url=url)
    yield store
    sql_db_memory.close_all_sql_managers()


def _drop_next_connection(store):
    """Make the next checkout hand out a connection the server has dropped.

    Closing the raw connection in the checkout event is what a restarted or
    idle-killed database looks like to a pool that does not ping.
    """
    manager = store._sql_manager
    state = {"dropped": 0}

    def drop(dbapi_connection, record, proxy):
        if state["dropped"] == 0:
            state["dropped"] += 1
            dbapi_connection.close()

    # Whichever pool the next operation uses, reads or writes.
    for engine in (manager.get_engine(), manager._read_engine):
        event.listen(engine, "checkout", drop)
    return state


def test_the_pool_does_not_ping_and_is_sized_explicitly(store):
    pool = store._sql_manager.get_engine().pool
    assert pool._pre_ping is False
    assert pool.size() == sql_db_memory.DEFAULT_POOL_SIZE
    assert pool._max_overflow == sql_db_memory.DEFAULT_MAX_OVERFLOW
    assert pool._recycle > 0


def test_the_pool_size_can_be_set_by_the_caller(tmp_path):
    url = f"sqlite:///{tmp_path / 'sized.db'}"
    store = DatabaseMessageStore(db_url=url, pool_size=3, max_overflow=4)
    try:
        pool = store._sql_manager.get_engine().pool
        assert (pool.size(), pool._max_overflow) == (3, 4)
    finally:
        sql_db_memory.close_all_sql_managers()


def test_the_pool_size_can_be_set_by_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_SQL_POOL_SIZE", "5")
    monkeypatch.setenv("OMNICOREAGENT_SQL_MAX_OVERFLOW", "6")
    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'env.db'}")
    try:
        pool = store._sql_manager.get_engine().pool
        assert (pool.size(), pool._max_overflow) == (5, 6)
    finally:
        sql_db_memory.close_all_sql_managers()


@pytest.mark.asyncio
async def test_a_stored_message_survives_a_dropped_connection(store):
    await store.store_message("user", "first", {"agent_name": "a"}, "s1")
    state = _drop_next_connection(store)

    await store.store_message("user", "second", {"agent_name": "a"}, "s1")

    assert state["dropped"] == 1
    contents = [m["content"] for m in await store.get_messages("s1", "a")]
    assert contents == ["first", "second"], "a dropped connection lost the message"


@pytest.mark.asyncio
async def test_a_read_survives_a_dropped_connection(store):
    await store.store_message("user", "first", {"agent_name": "a"}, "s1")
    state = _drop_next_connection(store)

    messages = await store.get_messages("s1", "a")

    assert state["dropped"] == 1
    assert [m["content"] for m in messages] == ["first"]


@pytest.mark.asyncio
async def test_run_state_survives_a_dropped_connection(store):
    version = await store.save_run_state({"run_id": "r1", "status": "running"}, None)
    state = _drop_next_connection(store)

    assert await store.save_run_state({"run_id": "r1", "status": "success"}, version) == 2

    assert state["dropped"] == 1
    assert (await store.get_run_state("r1"))["status"] == "success"


@pytest.mark.asyncio
async def test_a_write_is_not_retried_after_its_commit_began(store, monkeypatch):
    """A drop during COMMIT leaves it unknown whether the write landed, so
    the store does not write it a second time."""
    from sqlalchemy.exc import OperationalError

    real_commit = Session.commit
    calls = {"n": 0}

    def commit_then_drop(self):
        calls["n"] += 1
        real_commit(self)
        exc = OperationalError("COMMIT", {}, Exception("server closed the connection"))
        exc.connection_invalidated = True
        raise exc

    monkeypatch.setattr(Session, "commit", commit_then_drop)

    # The failure reaches the caller (a failed write is never swallowed).
    with pytest.raises(OperationalError):
        await store.store_message("user", "once", {"agent_name": "a"}, "s1")

    assert calls["n"] == 1
    monkeypatch.undo()
    contents = [m["content"] for m in await store.get_messages("s1", "a")]
    assert contents == ["once"]


@pytest.mark.asyncio
async def test_other_errors_are_not_retried(store, monkeypatch):
    from sqlalchemy.exc import OperationalError

    attempts = {"n": 0}
    real = Session.execute

    def broken(self, *args, **kwargs):
        attempts["n"] += 1
        raise OperationalError("SELECT", {}, Exception("syntax"))

    monkeypatch.setattr(Session, "execute", broken)
    monkeypatch.setattr(Session, "get", lambda self, *a, **k: broken(self))

    with pytest.raises(OperationalError):
        await store.get_run_state("r1")

    assert attempts["n"] == 1
    monkeypatch.setattr(Session, "execute", real)


@pytest.mark.asyncio
async def test_reads_run_without_a_transaction(store):
    """A read that opens a transaction ends it with a rollback round trip."""
    manager = store._sql_manager
    session = store._get_session(read_only=True)
    try:
        read_bind = session.get_bind()
    finally:
        store._release_session(session)
    session = store._get_session()
    try:
        write_bind = session.get_bind()
    finally:
        store._release_session(session)
    # Reads have a pool of autocommit connections of their own; writes keep
    # transactions.
    assert read_bind is manager._read_engine
    assert read_bind is not write_bind
    assert read_bind.dialect._on_connect_isolation_level == "AUTOCOMMIT"
    assert write_bind.dialect._on_connect_isolation_level is None
    # And reads still see what was written.
    await store.store_message("user", "hi", {"agent_name": "a"}, "s1")
    assert [m["content"] for m in await store.get_messages("s1", "a")] == ["hi"]


@pytest.mark.asyncio
async def test_clear_memory_does_not_block_the_event_loop(store, monkeypatch):
    import asyncio
    import threading

    await store.store_message("user", "hi", {"agent_name": "a"}, "s1")
    loop_thread = threading.get_ident()
    seen = {}
    real = store._get_session

    def spy(*args, **kwargs):
        seen["thread"] = threading.get_ident()
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "_get_session", spy)
    await store.clear_memory("s1")
    assert seen["thread"] != loop_thread
    assert await store.get_messages("s1", "a") == []
    await asyncio.sleep(0)
