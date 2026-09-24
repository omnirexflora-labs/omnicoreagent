"""Scale plan S4: two processes starting on one database at the same time.

SQLAlchemy's ``create_all`` looks for each table and creates what it did not
find. Two processes starting together both look, both find nothing, and both
create; on PostgreSQL the loser gets an integrity error against ``pg_type``,
and it took the whole process down at startup — the first two server processes
brought up on one database died on it.

Losing that race is not a failure, so these hold that it is survived: the
handler itself, and then several stores and indexes opening the same database
at once.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from omnicoreagent.background.store.sql import SqlTaskStore
from omnicoreagent.core.sql_schema import create_tables
from omnicoreagent.core.telemetry.archive_index import SqlTelemetryIndex

POSTGRES_URL_ENV = "OMNICOREAGENT_TEST_POSTGRES_URL"


@pytest.fixture
def url(tmp_path, request):
    """SQLite always; PostgreSQL too when one is configured."""
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'shared.db'}"
    configured = os.getenv(POSTGRES_URL_ENV)
    if not configured:
        pytest.skip(f"PostgreSQL schema race tests need {POSTGRES_URL_ENV}")
    return configured


def _both(function):
    return pytest.mark.parametrize("url", ["sqlite", "postgres"], indirect=True)(function)


@_both
def test_a_create_that_lost_the_race_is_not_an_error(url, tmp_path):
    """The tables are there; the create that failed saying so is survived."""
    from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

    prefix = f"t{uuid4().hex[:12]}_"
    metadata = MetaData()
    Table(
        f"{prefix}race",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String(32)),
    )
    engine = create_engine(url, future=True)
    try:
        create_tables(engine, metadata)  # the winner

        calls = {"count": 0}
        original = metadata.create_all

        def losing(*args, **kwargs):
            calls["count"] += 1
            raise RuntimeError("duplicate key value violates unique constraint")

        metadata.create_all = losing
        try:
            create_tables(engine, metadata)  # the loser: tables already exist
        finally:
            metadata.create_all = original
        assert calls["count"] == 1, "the loser should not have to try twice"

        # And when the tables really are missing, the error is not swallowed.
        missing = MetaData()
        Table(f"{prefix}absent", missing, Column("id", Integer, primary_key=True))
        missing.create_all = losing
        with pytest.raises(RuntimeError):
            create_tables(engine, missing, attempts=1)
    finally:
        metadata.drop_all(engine)
        engine.dispose()


@_both
@pytest.mark.asyncio
async def test_many_task_stores_open_one_database_at_once(url):
    prefix = f"t{uuid4().hex[:12]}_"
    stores = [SqlTaskStore(url, table_prefix=prefix) for _ in range(6)]
    try:
        # All six at once, the way six processes would start together.
        await asyncio.gather(*(store.initialize() for store in stores))
        assert await stores[0].list_agents() == []
    finally:
        await asyncio.to_thread(
            stores[0]._tables.metadata.drop_all, stores[0]._engine
        )
        for store in stores:
            await store.close()


@_both
def test_many_telemetry_indexes_open_one_database_at_once(url):
    prefix = f"t{uuid4().hex[:12]}_"
    indexes = [SqlTelemetryIndex(url, table_prefix=prefix) for _ in range(6)]
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            # max_cursor opens the index, so six threads race the create.
            assert list(pool.map(lambda index: index.max_cursor(), indexes)) == [0] * 6
    finally:
        indexes[0].drop()
        for index in indexes:
            index.close()
