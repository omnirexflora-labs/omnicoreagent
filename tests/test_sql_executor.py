"""The SQL store's database waits run on threads of their own.

Every database call hops to a thread, and the threads were the event loop's
default executor, shared with every other ``asyncio.to_thread`` in the
process: at most ``min(32, CPUs + 4)`` of them, 6 on a 2-core container,
whatever the connection pool allowed (the support desk ramp, 2026-10-07: most
active samples were database threads, and the event loop was busy only 18% of
the time). The store now has an executor sized to its pool, so database waits
neither queue behind a tool's file read nor hold it up.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_run_state import _sql_store


@pytest.mark.asyncio
async def test_database_calls_do_not_wait_behind_a_full_default_executor(tmp_path):
    store = _sql_store(tmp_path)
    loop = asyncio.get_running_loop()
    default = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(default)
    release = threading.Event()
    try:
        # Two blocking jobs hold every thread the default executor has.
        blockers = [loop.run_in_executor(None, release.wait) for _ in range(2)]
        await store.store_message("user", "hello", {}, "s1")
        messages = await asyncio.wait_for(store.get_messages("s1"), timeout=10)
        assert [m["content"] for m in messages] == ["hello"]
    finally:
        release.set()
        await asyncio.gather(*blockers)
        default.shutdown()


@pytest.mark.asyncio
async def test_the_executor_is_as_big_as_the_connection_pool(tmp_path):
    pytest.importorskip("sqlalchemy")
    from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

    store = DatabaseMessageStore(
        db_url=f"sqlite:///{tmp_path / 'sized.db'}", pool_size=3, max_overflow=4
    )
    await store.store_message("user", "hi", {}, "s2")
    executor = store._sql_manager.executor()
    assert executor._max_workers == 7


@pytest.mark.asyncio
async def test_a_context_variable_set_by_the_caller_is_seen_on_the_database_thread(tmp_path):
    # to_thread copies the caller's context; so must the store's own executor,
    # or what the runtime sets for a run (its trace, its budgets) is lost there.
    store = _sql_store(tmp_path)
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    seen: list[str] = []

    from sqlalchemy import event

    engine = store._sql_manager.get_engine()

    @event.listens_for(engine, "before_cursor_execute")
    def look(conn, cursor, statement, parameters, context, executemany):
        seen.append(marker.get())

    marker.set("mine")
    await store.store_message("user", "hello", {}, "s3")
    assert seen and set(seen) == {"mine"}


@pytest.mark.asyncio
async def test_closing_the_last_store_stops_its_threads(tmp_path):
    from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'closed.db'}")
    await store.store_message("user", "hi", {}, "s4")
    manager = store._sql_manager
    executor = manager.executor()
    await store.close()
    assert executor._shutdown is True
