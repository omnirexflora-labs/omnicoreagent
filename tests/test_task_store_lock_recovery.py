"""A task-store lock left by a dead process does not stop the next one.

Found by P2 of the production proving plan: the steward's container was
recreated while the worker held the Redis task-store lock, whose lease was
five minutes; every restart gave up after thirty seconds — "Timed out
acquiring Redis task-store lock" — and the deployment crash-looped until the
lease lapsed. MongoDB still takes a lock over the whole store, so its lease is
short (its operations take milliseconds; a long write refreshes it),
acquisition always outlasts a full lease, and a lock still held after that
names how long the holder has left.

Redis no longer has the failure mode at all: since scale plan S2b it writes an
entity per key under optimistic transactions, and takes no lock over the store,
so there is nothing for a dead process to leave behind. That is what the Redis
tests here hold now — including that a lock key left by the old store is simply
ignored.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

from omnicoreagent.background.models import BackgroundAgentSpec
from omnicoreagent.background.store.mongodb import MongoDbTaskStore
from omnicoreagent.background.store.redis import RedisTaskStore

REDIS_URL = os.getenv("OMNICOREAGENT_TEST_REDIS_URL", "redis://127.0.0.1:16379/0")
MONGODB_URI = os.getenv("OMNICOREAGENT_TEST_MONGODB_URI", "mongodb://127.0.0.1:27027")
MONGODB_DATABASE = os.getenv("OMNICOREAGENT_TEST_MONGODB_DATABASE", "omnicoreagent_test")

pytestmark = pytest.mark.asyncio


async def _redis_store(**lock) -> RedisTaskStore:
    store = RedisTaskStore(
        url=REDIS_URL, prefix=f"test:lock:{uuid4().hex}", connect_timeout=1.0, **lock
    )
    try:
        await store.initialize()
    except Exception as exc:  # noqa: BLE001 - the backend is optional in CI
        pytest.skip(f"Redis is not reachable at {REDIS_URL}: {exc}")
    return store


async def _mongo_store(**lock) -> MongoDbTaskStore:
    store = MongoDbTaskStore(
        uri=MONGODB_URI,
        database=MONGODB_DATABASE,
        collection_prefix=f"test_lock_{uuid4().hex}",
        connect_timeout=1.0,
        **lock,
    )
    try:
        await store.initialize()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB is not reachable at {MONGODB_URI}: {exc}")
    return store


async def test_the_default_mongodb_lease_is_short_and_acquisition_outlasts_it():
    store = MongoDbTaskStore(uri="mongodb://x", database="d")
    assert store.lock_lease_seconds <= 30
    assert store.lock_wait_seconds >= store.lock_lease_seconds


async def test_redis_takes_no_lock_over_the_store():
    """Nothing to inherit: a dead process leaves no lock, because none is taken."""
    store = await _redis_store()
    try:
        # A lock key the old store would have left behind, lease still running.
        await store._client.set(f"{store.prefix}:lock", "dead-process", px=600_000)

        started = time.monotonic()
        await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
        waited = time.monotonic() - started

        assert waited < 1, "a write waited for something; nothing should hold it"
        assert (await store.get_agent("agent")) is not None
    finally:
        await store._client.delete(f"{store.prefix}:lock")
        await store.close()


async def test_two_redis_stores_write_at_the_same_time():
    """What the lock used to serialize: both get through, neither waits."""
    import asyncio

    first = await _redis_store()
    second = RedisTaskStore(url=REDIS_URL, prefix=first.prefix, connect_timeout=1.0)
    await second.initialize()
    try:
        started = time.monotonic()
        await asyncio.gather(
            *(
                store.save_agent(BackgroundAgentSpec(agent_id=f"agent-{number}"))
                for number, store in enumerate((first, second, first, second))
            )
        )
        assert time.monotonic() - started < 2
        assert {agent.agent_id for agent in await first.list_agents()} == {
            "agent-0",
            "agent-1",
            "agent-2",
            "agent-3",
        }
    finally:
        keys = [key async for key in first._client.scan_iter(match=f"{first.prefix}:*")]
        if keys:
            await first._client.delete(*keys)
        await first.close()
        await second.close()


async def test_mongodb_waits_out_a_dead_holders_lease():
    store = await _mongo_store(lock_timeout=0.1, lock_lease_seconds=0.6)
    try:
        from datetime import timedelta

        from omnicoreagent.background.models import utc_now

        await store._lock_collection.update_one(
            {"_id": "task_store"},
            {"$set": {"token": "dead-process", "expires_at": utc_now() + timedelta(seconds=0.6)}},
            upsert=True,
        )

        started = time.monotonic()
        await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
        waited = time.monotonic() - started

        assert 0.4 < waited < 3
        assert (await store.get_agent("agent")) is not None
    finally:
        await store.close()
