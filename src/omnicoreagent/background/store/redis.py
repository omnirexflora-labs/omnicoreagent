"""Redis-backed task store for background execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import time
from typing import TypeVar
from uuid import uuid4

from omnicoreagent.background.errors import InvalidTaskStoreError, TaskStoreError
from omnicoreagent.background.store.serialized import SerializedTaskStore


T = TypeVar("T")


class RedisTaskStore(SerializedTaskStore):
    """Durable Redis task store using versioned record snapshots."""

    def __init__(
        self,
        url: str,
        *,
        prefix: str | None = None,
        connect_timeout: float | None = None,
        lock_timeout: float = 30.0,
        lock_lease_seconds: float = 300.0,
    ) -> None:
        super().__init__()
        self.url = url
        self.prefix = (prefix or "omnicoreagent:background").rstrip(":")
        self.connect_timeout = connect_timeout
        self.lock_timeout = lock_timeout
        self.lock_lease_seconds = lock_lease_seconds
        self._client = None
        self._backend_init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await super().initialize()
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise InvalidTaskStoreError(
                "Redis task store requires the redis extra: "
                "pip install 'omnicoreagent[redis]'"
            ) from exc

        kwargs = {}
        if self.connect_timeout is not None:
            kwargs["socket_connect_timeout"] = self.connect_timeout
        self._client = Redis.from_url(self.url, decode_responses=True, **kwargs)
        await self._client.ping()
        await self._load_backend_state()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().close()

    async def _load_backend_state(self) -> None:
        await self._ensure_initialized()
        client = self._require_client()
        generation = await client.get(self._active_generation_key)
        if not generation:
            await self._load_snapshot(None)
            return
        await self._load_snapshot(
            {
                "agents": await self._load_hash(client, generation, "agents"),
                "tasks": await self._load_hash(client, generation, "tasks"),
                "schedule_states": await self._load_hash(
                    client, generation, "schedule_states"
                ),
                "runs": await self._load_hash(client, generation, "runs"),
                "attempts": await self._load_hash(client, generation, "attempts"),
                "cancel_requested": sorted(
                    await client.smembers(self._generation_key(generation, "cancelled"))
                ),
            }
        )

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
        client = self._require_client()
        snapshot = await self._snapshot()
        generation = uuid4().hex
        previous_generation = await client.get(self._active_generation_key)
        stale_generation = await client.get(self._previous_generation_key)

        await self._refresh_lock(token)
        try:
            for name in ("agents", "tasks", "schedule_states", "runs", "attempts"):
                key = self._generation_key(generation, name)
                records = snapshot[name]
                if records:
                    await client.hset(
                        key,
                        mapping={
                            record_id: json.dumps(value)
                            for record_id, value in records.items()
                        },
                    )
            cancelled = snapshot["cancel_requested"]
            if cancelled:
                await client.sadd(
                    self._generation_key(generation, "cancelled"), *cancelled
                )
            await self._commit_generation(token, generation, previous_generation)
        except Exception:
            await self._delete_generation(generation)
            raise
        if stale_generation and stale_generation not in {previous_generation, generation}:
            await self._delete_generation(stale_generation)

    async def _acquire_lock(self) -> str:
        client = self._require_client()
        token = uuid4().hex
        deadline = time.monotonic() + self.lock_timeout
        while time.monotonic() < deadline:
            acquired = await client.set(
                self._lock_key,
                token,
                nx=True,
                px=self._lock_lease_ms,
            )
            if acquired:
                return token
            await asyncio.sleep(0.05)
        raise TaskStoreError("Timed out acquiring Redis task-store lock")

    async def _refresh_lock(self, token: str) -> None:
        client = self._require_client()
        refreshed = await client.eval(
            """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                return redis.call("PEXPIRE", KEYS[1], ARGV[2])
            end
            return 0
            """,
            1,
            self._lock_key,
            token,
            self._lock_lease_ms,
        )
        if not refreshed:
            raise TaskStoreError("Redis task-store lock expired before commit")

    async def _commit_generation(
        self, token: str, generation: str, previous_generation: str | None
    ) -> None:
        client = self._require_client()
        committed = await client.eval(
            """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                redis.call("SET", KEYS[2], ARGV[2])
                if ARGV[3] ~= "" then
                    redis.call("SET", KEYS[3], ARGV[3])
                end
                return 1
            end
            return 0
            """,
            3,
            self._lock_key,
            self._active_generation_key,
            self._previous_generation_key,
            token,
            generation,
            previous_generation or "",
        )
        if not committed:
            raise TaskStoreError("Redis task-store lock expired before commit")

    async def _release_lock(self, token: str) -> None:
        client = self._require_client()
        await client.eval(
            """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                return redis.call("DEL", KEYS[1])
            end
            return 0
            """,
            1,
            self._lock_key,
            token,
        )

    def _require_client(self):
        if self._client is None:
            raise TaskStoreError("Redis task store is not initialized")
        return self._client

    async def _ensure_initialized(self) -> None:
        if self._client is not None:
            return
        async with self._backend_init_lock:
            if self._client is None:
                await self.initialize()

    async def _load_hash(self, client, generation: str, name: str) -> dict:
        raw = await client.hgetall(self._generation_key(generation, name))
        return {record_id: json.loads(value) for record_id, value in raw.items()}

    def _generation_key(self, generation: str, name: str) -> str:
        return f"{self.prefix}:generation:{generation}:{name}"

    async def _delete_generation(self, generation: str) -> None:
        client = self._require_client()
        await client.delete(
            *[
                self._generation_key(generation, name)
                for name in (
                    "agents",
                    "tasks",
                    "schedule_states",
                    "runs",
                    "attempts",
                    "cancelled",
                )
            ]
        )

    @property
    def _active_generation_key(self) -> str:
        return f"{self.prefix}:active_generation"

    @property
    def _previous_generation_key(self) -> str:
        return f"{self.prefix}:previous_generation"

    @property
    def _lock_key(self) -> str:
        return f"{self.prefix}:lock"

    @property
    def _lock_lease_ms(self) -> int:
        return int(self.lock_lease_seconds * 1000)
