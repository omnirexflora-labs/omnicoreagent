"""A message the store could not write is an error, not a log line.

The SQL, Redis and MongoDB stores caught every failure of ``store_message``,
logged it and returned. The run then carried on as if the message had been
saved: a user's request or an assistant's answer that was in no history, with
nothing in the run's record to say so. (Found while merging the P6 tracks,
2026-10-07: the run's own request is now kept on its record, but the history
write still looked like it worked.) The write now raises, and the run fails
with the store's error instead of losing the message.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from omnicoreagent.core.memory_store import sql_db_memory
from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

from test_run_recovery import _agent
from test_run_suspend import RecordingModel


class DiskFull(Exception):
    """Stands in for any write error that is not a dropped connection."""


@pytest.fixture
def store(tmp_path):
    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'messages.db'}")
    yield store
    sql_db_memory.close_all_sql_managers()


def _fail_commits(monkeypatch):
    def commit(self):
        raise OperationalError("COMMIT", {}, Exception("disk is full"))

    monkeypatch.setattr(Session, "commit", commit)


@pytest.mark.asyncio
async def test_sql_store_message_raises_when_the_write_fails(store, monkeypatch):
    _fail_commits(monkeypatch)

    with pytest.raises(OperationalError, match="disk is full"):
        await store.store_message("user", "refund 77", {"agent_name": "a"}, "s1")

    monkeypatch.undo()
    assert await store.get_messages("s1", "a") == []


@pytest.mark.asyncio
async def test_mongodb_store_message_raises_when_the_write_fails():
    from omnicoreagent.core.memory_store.mongodb import MongoDb

    class Collection:
        async def insert_one(self, document):
            raise DiskFull("write concern failed")

    store = MongoDb(uri="mongodb://unused", db_name="d", collection="c")
    store._initialized = True
    store.collection = Collection()

    with pytest.raises(DiskFull):
        await store.store_message("user", "refund 77", {}, "s1")


@pytest.mark.asyncio
async def test_redis_store_message_raises_when_the_write_fails():
    from omnicoreagent.core.memory_store.redis_memory import RedisMemoryStore

    class Client:
        async def zadd(self, key, mapping):
            raise DiskFull("READONLY You can't write against a read only replica")

    store = RedisMemoryStore(redis_url=None)
    store._redis_client = Client()

    with pytest.raises(DiskFull):
        await store.store_message("user", "refund 77", {}, "s1")


@pytest.mark.asyncio
async def test_redis_store_message_raises_when_there_is_no_connection():
    from omnicoreagent.core.memory_store.redis_memory import RedisMemoryStore

    store = RedisMemoryStore(redis_url=None)

    with pytest.raises(RuntimeError, match="Redis not configured"):
        await store.store_message("user", "refund 77", {}, "s1")


@pytest.mark.asyncio
async def test_in_memory_store_message_cannot_fail_quietly():
    # A list append: it either happens or raises, and there is no handler.
    store = InMemoryStore()
    await store.store_message("user", "hi", {}, "s1")
    assert [m["content"] for m in await store.get_messages("s1")] == ["hi"]


def _fail_stores(agent, monkeypatch, *, role):
    real = agent.memory_router.memory_store.store_message
    state = {"failed": 0}

    async def store_message(r, content, metadata, session_id):
        if r == role:
            state["failed"] += 1
            raise DiskFull("disk is full")
        return await real(r, content, metadata, session_id)

    monkeypatch.setattr(agent.memory_router.memory_store, "store_message", store_message)
    return state


@pytest.mark.asyncio
async def test_a_run_whose_request_could_not_be_stored_fails_without_answering(monkeypatch):
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    model = RecordingModel("an answer")
    agent = await _agent(model, ToolRegistry())
    _fail_stores(agent, monkeypatch, role="user")

    with pytest.raises(DiskFull):
        await agent.run("refund order 77", session_id="s", run_id="run_u")

    record = await agent.get_run("run_u")
    assert record["status"] == "failed"
    assert record["error"]["type"] == "DiskFull"
    assert record["request"]["content"] == "refund order 77"


@pytest.mark.asyncio
async def test_a_run_whose_answer_could_not_be_stored_fails_and_keeps_the_answer(monkeypatch):
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    model = RecordingModel("refunded order 77")
    agent = await _agent(model, ToolRegistry())
    _fail_stores(agent, monkeypatch, role="assistant")

    with pytest.raises(DiskFull):
        await agent.run("refund order 77", session_id="s", run_id="run_a")

    record = await agent.get_run("run_a")
    assert record["status"] == "failed"
    assert record["error"]["type"] == "DiskFull"
    # The record still holds what the model said, so it is not lost with the
    # history write.
    answers = [m["content"] for m in record["context"]["messages"] if m["role"] == "assistant"]
    assert answers == ["refunded order 77"]
