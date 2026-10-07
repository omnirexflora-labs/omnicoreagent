"""Fifty concurrent first calls open one MongoDB connection, not fifty.

``_ensure_connected`` checked ``_initialized`` and then awaited a ping, so every
call that arrived before the first finished opened its own client, and all but
the last were leaked (found merging the P6 tracks, 2026-10-07).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("motor")

from omnicoreagent.core.memory_store import mongodb as mongodb_module
from omnicoreagent.core.memory_store.mongodb import MongoDb


class FakeCollection:
    async def create_indexes(self, indexes):
        await asyncio.sleep(0)

    async def insert_one(self, document):
        await asyncio.sleep(0)


class FakeDatabase:
    def __getitem__(self, name):
        return FakeCollection()


class FakeAdmin:
    async def command(self, name):
        await asyncio.sleep(0.01)  # the window the race lived in


class FakeClient:
    opened: list["FakeClient"] = []

    def __init__(self, uri):
        self.admin = FakeAdmin()
        self.closed = False
        FakeClient.opened.append(self)

    def __getitem__(self, name):
        return FakeDatabase()

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    FakeClient.opened = []
    monkeypatch.setattr(mongodb_module, "AsyncIOMotorClient", FakeClient)


@pytest.mark.asyncio
async def test_fifty_concurrent_first_calls_open_one_connection():
    store = MongoDb(uri="mongodb://unused", db_name="d", collection="c")

    await asyncio.gather(*(store.store_message("user", f"m{i}", {}, "s") for i in range(50)))

    assert len(FakeClient.opened) == 1
    assert not FakeClient.opened[0].closed


@pytest.mark.asyncio
async def test_a_failed_connect_is_tried_again_by_the_next_call():
    store = MongoDb(uri="mongodb://unused", db_name="d", collection="c")
    real_command = FakeAdmin.command
    state = {"fail": True}

    async def command(self, name):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("not reachable yet")
        return await real_command(self, name)

    FakeAdmin.command = command
    try:
        with pytest.raises(RuntimeError):
            await store._ensure_connected()
        await store._ensure_connected()
    finally:
        FakeAdmin.command = real_command

    assert len(FakeClient.opened) == 2
    assert FakeClient.opened[0].closed and not FakeClient.opened[1].closed
