"""Closing a SQL memory store releases its share of the connection pool.

Found writing Stores and scale (D7): `await MemoryRouter("sql").close()` did
nothing, though the router's close says it closes SQL. Stores that name the
same database share one pool, so closing one must not close the pool another
still uses: the pool is disposed when the last store using it closes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from omnicoreagent.core.memory_store import sql_db_memory
from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore


@pytest.mark.asyncio
async def test_the_pool_closes_with_the_last_store_that_uses_it(tmp_path):
    url = f"sqlite:///{tmp_path / 'memory.db'}"
    first, second = DatabaseMessageStore(db_url=url), DatabaseMessageStore(db_url=url)
    manager = sql_db_memory.get_sql_manager(url)

    await first.close()
    assert manager.get_engine() is not None, "the other store still uses the pool"
    await second.get_messages("s1")  # still works

    await second.close()
    assert manager.get_engine() is None
    assert url not in sql_db_memory._sql_managers


@pytest.mark.asyncio
async def test_the_memory_router_closes_its_sql_store(tmp_path, monkeypatch):
    from omnicoreagent import MemoryRouter

    url = f"sqlite:///{tmp_path / 'router.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    router = MemoryRouter("sql")

    await router.close()

    assert url not in sql_db_memory._sql_managers
