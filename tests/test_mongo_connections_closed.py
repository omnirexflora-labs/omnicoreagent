"""The MongoDB memory store closes its connections.

Found when the test server's MongoDB stopped accepting connections ("Too
many open files", 2026-09-26): the store opened a client (a connection pool
and its monitors) and nothing could close it, and a failed connection
attempt left its client open before the next attempt opened another. An
application building a store per request leaked the same way. The store and
the memory router now close, and a failed attempt closes what it opened.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omnicoreagent.core.memory_store.mongodb import MongoDb


def _client(ping_fails: bool = False) -> MagicMock:
    client = MagicMock()
    client.admin.command = AsyncMock(side_effect=RuntimeError("no server") if ping_fails else None)
    collection = MagicMock()
    collection.create_indexes = AsyncMock()
    client.__getitem__.return_value.__getitem__.return_value = collection
    return client


@pytest.mark.asyncio
async def test_close_closes_the_client_and_a_later_call_reconnects():
    first, second = _client(), _client()
    with patch("omnicoreagent.core.memory_store.mongodb.AsyncIOMotorClient", side_effect=[first, second]):
        store = MongoDb(uri="mongodb://db:27017", db_name="t", collection="messages")
        await store._ensure_connected()

        await store.close()
        first.close.assert_called_once()

        await store._ensure_connected()  # used again after closing: a new client
        assert store.client is second


@pytest.mark.asyncio
async def test_a_failed_connection_attempt_closes_its_client():
    failing = _client(ping_fails=True)
    with patch("omnicoreagent.core.memory_store.mongodb.AsyncIOMotorClient", return_value=failing):
        store = MongoDb(uri="mongodb://db:27017", db_name="t", collection="messages")
        with pytest.raises(Exception):
            await store._ensure_connected()

    failing.close.assert_called_once()
    assert store.client is None


@pytest.mark.asyncio
async def test_the_memory_router_closes_its_store():
    from omnicoreagent import MemoryRouter

    router = MemoryRouter("in_memory")
    router.memory_store = MagicMock(close=AsyncMock())

    await router.close()

    router.memory_store.close.assert_awaited_once()
