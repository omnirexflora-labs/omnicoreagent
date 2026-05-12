"""Task-store router for background execution."""

from __future__ import annotations

import os
from typing import Any

from omnicoreagent.background.errors import InvalidTaskStoreError
from omnicoreagent.background.models import TaskStoreBackend, TaskStoreConfig
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.store.in_memory import InMemoryTaskStore
from omnicoreagent.background.store.mongodb import MongoDbTaskStore
from omnicoreagent.background.store.redis import RedisTaskStore
from omnicoreagent.background.store.sql import SqlTaskStore


class TaskStoreRouter:
    """Build task-store implementations from public backend configuration."""

    @classmethod
    def create(cls, config: str | dict[str, Any] | AbstractTaskStore | None = None):
        if isinstance(config, AbstractTaskStore):
            return config

        store_config = cls.normalize_config(config)
        if store_config.backend == TaskStoreBackend.IN_MEMORY:
            return InMemoryTaskStore()

        if store_config.backend == TaskStoreBackend.SQL:
            return SqlTaskStore(url=store_config.url)
        if store_config.backend == TaskStoreBackend.REDIS:
            return RedisTaskStore(
                url=store_config.url or "",
                prefix=store_config.prefix,
                connect_timeout=store_config.connect_timeout,
            )
        if store_config.backend == TaskStoreBackend.MONGODB:
            return MongoDbTaskStore(
                uri=store_config.uri or "",
                database=store_config.database or "",
                collection_prefix=store_config.collection_prefix or store_config.prefix,
                connect_timeout=store_config.connect_timeout,
            )
        raise InvalidTaskStoreError(f"Unsupported task store backend: {store_config.backend}")

    @classmethod
    def normalize_config(
        cls, config: str | dict[str, Any] | None = None
    ) -> TaskStoreConfig:
        try:
            if config is None:
                return TaskStoreConfig(
                    backend=TaskStoreBackend.IN_MEMORY,
                )
            if isinstance(config, str):
                if config not in {
                    TaskStoreBackend.IN_MEMORY.value,
                    TaskStoreBackend.SQL.value,
                    TaskStoreBackend.REDIS.value,
                    TaskStoreBackend.MONGODB.value,
                }:
                    raise InvalidTaskStoreError(
                        "task_store must be 'in_memory', 'sql', 'redis', or 'mongodb'"
                    )
                backend = TaskStoreBackend(config)
                if backend == TaskStoreBackend.REDIS:
                    return TaskStoreConfig(
                        backend=backend,
                        url=os.getenv("REDIS_URL"),
                    )
                if backend == TaskStoreBackend.MONGODB:
                    return TaskStoreConfig(
                        backend=backend,
                        uri=os.getenv("MONGODB_URI"),
                        database=os.getenv(
                            "OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE",
                            os.getenv("MONGODB_DATABASE", "omnicoreagent"),
                        ),
                    )
                return TaskStoreConfig(backend=backend)
            if isinstance(config, dict):
                return TaskStoreConfig(**config)
        except ValueError as exc:
            raise InvalidTaskStoreError(str(exc)) from exc
        raise InvalidTaskStoreError("task_store must be a backend name, config, or store")
