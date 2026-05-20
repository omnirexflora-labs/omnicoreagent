"""MongoDB-backed task store for background execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import time
from typing import TypeVar
from uuid import uuid4

from omnicoreagent.background.errors import InvalidTaskStoreError, TaskStoreError
from omnicoreagent.background.models import utc_now
from omnicoreagent.background.store.serialized import SerializedTaskStore


T = TypeVar("T")


class MongoDbTaskStore(SerializedTaskStore):
    """Durable MongoDB task store using versioned record snapshots."""

    def __init__(
        self,
        uri: str,
        database: str,
        *,
        collection_prefix: str | None = None,
        connect_timeout: float | None = None,
        lock_timeout: float = 30.0,
        lock_lease_seconds: float = 300.0,
    ) -> None:
        super().__init__()
        self.uri = uri
        self.database_name = database
        self.collection_prefix = collection_prefix or "omnicoreagent_background"
        self.connect_timeout = connect_timeout
        self.lock_timeout = lock_timeout
        self.lock_lease_seconds = lock_lease_seconds
        self._client = None
        self._db = None
        self._backend_init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await super().initialize()
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            from pymongo.write_concern import WriteConcern
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise InvalidTaskStoreError(
                "MongoDB task store requires the mongodb extra: "
                "pip install 'omnicoreagent[mongodb]'"
            ) from exc

        kwargs = {}
        if self.connect_timeout is not None:
            kwargs["serverSelectionTimeoutMS"] = int(self.connect_timeout * 1000)
        self._client = AsyncIOMotorClient(self.uri, **kwargs)
        self._db = self._client[self.database_name].with_options(
            write_concern=WriteConcern("majority")
        )
        await self._db.command("ping")
        await self._lock_collection.update_one(
            {"_id": "task_store"},
            {
                "$setOnInsert": {
                    "token": None,
                    "expires_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )
        await self._load_backend_state()

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
        await super().close()

    async def _load_backend_state(self) -> None:
        await self._ensure_initialized()
        state = await self._lock_collection.find_one({"_id": "task_store"})
        generation = state.get("active_generation") if state else None
        if not generation:
            await self._load_snapshot(None)
            return
        await self._load_snapshot(await self._load_snapshot_records(generation))

    async def _mutate(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self._ensure_initialized()
        async with self._operation_lock:
            token = await self._acquire_lock()
            try:
                await self._load_backend_state()
                result = await operation()
                await self._persist_backend_state(token)
                return result
            finally:
                await self._release_lock(token)

    async def _read(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self._ensure_initialized()
        async with self._operation_lock:
            token = await self._acquire_lock()
            try:
                await self._load_backend_state()
                return await operation()
            finally:
                await self._release_lock(token)

    async def _persist_backend_state(self, token: str) -> None:
        snapshot = await self._snapshot()
        generation = uuid4().hex
        state = await self._lock_collection.find_one({"_id": "task_store"})
        previous_generation = state.get("active_generation") if state else None
        stale_generation = state.get("previous_generation") if state else None

        await self._refresh_lock(token)
        try:
            created_at = utc_now()
            records = []
            for category in (
                "agents",
                "tasks",
                "schedule_states",
                "runs",
                "attempts",
            ):
                records.extend(
                    {
                        "_id": f"{generation}:{category}:{record_id}",
                        "_generation": generation,
                        "category": category,
                        "record_id": record_id,
                        "value": value,
                        "created_at": created_at,
                    }
                    for record_id, value in snapshot[category].items()
                )
            records.extend(
                {
                    "_id": f"{generation}:cancel_requested:{run_id}",
                    "_generation": generation,
                    "category": "cancel_requested",
                    "record_id": run_id,
                    "created_at": created_at,
                }
                for run_id in snapshot["cancel_requested"]
            )
            if records:
                await self._snapshot_collection.insert_many(records)
            await self._commit_generation(token, generation, previous_generation)
        except Exception:
            await self._delete_generation(generation)
            raise
        if stale_generation and stale_generation not in {previous_generation, generation}:
            await self._delete_generation(stale_generation)

    async def _acquire_lock(self) -> str:
        token = uuid4().hex
        deadline = time.monotonic() + self.lock_timeout
        while time.monotonic() < deadline:
            now = utc_now()
            expires_at = now + timedelta(seconds=self.lock_lease_seconds)
            result = await self._lock_collection.update_one(
                {
                    "_id": "task_store",
                    "$or": [
                        {"token": None},
                        {"token": {"$exists": False}},
                        {"expires_at": {"$lte": now}},
                        {"token": token},
                    ],
                },
                {
                    "$set": {
                        "token": token,
                        "expires_at": expires_at,
                        "updated_at": now,
                    }
                },
            )
            if result.matched_count:
                return token
            await asyncio.sleep(0.05)
        raise TaskStoreError("Timed out acquiring MongoDB task-store lock")

    async def _refresh_lock(self, token: str) -> None:
        result = await self._lock_collection.update_one(
            {"_id": "task_store", "token": token},
            {
                "$set": {
                    "expires_at": utc_now()
                    + timedelta(seconds=self.lock_lease_seconds),
                    "updated_at": utc_now(),
                }
            },
        )
        if not result.matched_count:
            raise TaskStoreError("MongoDB task-store lock expired before commit")

    async def _commit_generation(
        self, token: str, generation: str, previous_generation: str | None
    ) -> None:
        await self._refresh_lock(token)
        result = await self._lock_collection.update_one(
            {"_id": "task_store", "token": token},
            {
                "$set": {
                    "active_generation": generation,
                    "previous_generation": previous_generation,
                    "updated_at": utc_now(),
                }
            },
        )
        if not result.matched_count:
            raise TaskStoreError("MongoDB task-store lock expired before commit")

    async def _release_lock(self, token: str) -> None:
        await self._lock_collection.update_one(
            {"_id": "task_store", "token": token},
            {"$set": {"token": None, "expires_at": utc_now(), "updated_at": utc_now()}},
        )

    @property
    def _lock_collection(self):
        return self._collection("locks")

    @property
    def _snapshot_collection(self):
        return self._collection("snapshots")

    def _collection(self, name: str):
        if self._db is None:
            raise TaskStoreError("MongoDB task store is not initialized")
        return self._db[f"{self.collection_prefix}_{name}"]

    async def _ensure_initialized(self) -> None:
        if self._db is not None:
            return
        async with self._backend_init_lock:
            if self._db is None:
                await self.initialize()

    async def _load_snapshot_records(self, generation: str) -> dict:
        docs = await self._snapshot_collection.find(
            {"_generation": generation}
        ).to_list(length=None)
        snapshot = {
            "agents": {},
            "tasks": {},
            "schedule_states": {},
            "runs": {},
            "attempts": {},
            "cancel_requested": [],
        }
        for doc in docs:
            category = doc["category"]
            if category == "cancel_requested":
                snapshot["cancel_requested"].append(doc["record_id"])
                continue
            snapshot[category][doc["record_id"]] = doc["value"]
        snapshot["cancel_requested"].sort()
        return snapshot

    async def _delete_generation(self, generation: str) -> None:
        await self._snapshot_collection.delete_many({"_generation": generation})
