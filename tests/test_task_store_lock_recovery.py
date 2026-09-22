"""A task-store lock left by a dead process does not stop the next one.

Found by P2 of the production proving plan: the steward's container was
recreated while the worker held the Redis task-store lock, whose lease was
five minutes; every restart gave up after thirty seconds — "Timed out
acquiring Redis task-store lock" — and the deployment crash-looped until the
lease lapsed. A store's lock lease is now short (its operations take
milliseconds; a long write refreshes it), acquisition always outlasts a full
lease, and a lock that is still held after that names how long the holder
has left. The same holds for MongoDB.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

from omnicoreagent.background.errors import TaskStoreError
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


def test_the_default_lease_is_short_and_acquisition_outlasts_it():
    for store in (RedisTaskStore(url="redis://x"), MongoDbTaskStore(uri="mongodb://x", database="d")):
        assert store.lock_lease_seconds <= 30
        assert store.lock_wait_seconds >= store.lock_lease_seconds


async def test_redis_waits_out_a_dead_holders_lease():
    store = await _redis_store(lock_timeout=0.1, lock_lease_seconds=0.6)
    try:
        # A process that died mid-operation: its token, its lease still running.
        await store._client.set(store._lock_key, "dead-process", px=600)

        started = time.monotonic()
        await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
        waited = time.monotonic() - started

        assert 0.4 < waited < 3, "waited for the lease to lapse, then went on"
        assert (await store.get_agent("agent")) is not None
    finally:
        await store.close()


async def test_redis_names_a_live_holder_when_it_gives_up():
    store = await _redis_store(lock_timeout=0.1, lock_lease_seconds=0.3)
    try:
        await store._client.set(store._lock_key, "live-process", px=5_000)
        with pytest.raises(TaskStoreError, match=r"held by another process.*\d+(\.\d+)?s"):
            await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
    finally:
        await store._client.delete(store._lock_key)
        await store.close()


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
