"""Task-store router for background execution."""

from __future__ import annotations

from typing import Any

from omnicoreagent.background.errors import InvalidTaskStoreError
from omnicoreagent.background.models import TaskStoreBackend, TaskStoreConfig
from omnicoreagent.background.store.base import AbstractTaskStore
from omnicoreagent.background.store.in_memory import InMemoryTaskStore
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
            raise InvalidTaskStoreError(
                "Redis task store is not implemented yet. Use task_store='sql' or 'in_memory'."
            )
        if store_config.backend == TaskStoreBackend.MONGODB:
            raise InvalidTaskStoreError(
                "MongoDB task store is not implemented yet. Use task_store='sql' or 'in_memory'."
            )
        raise InvalidTaskStoreError(f"Unsupported task store backend: {store_config.backend}")

    @classmethod
    def normalize_config(
        cls, config: str | dict[str, Any] | None = None
    ) -> TaskStoreConfig:
        if config is None:
            return TaskStoreConfig(
                backend=TaskStoreBackend.SQL,
                url="sqlite:///.omnicoreagent/background.db",
            )
        if isinstance(config, str):
            if config not in {
                TaskStoreBackend.IN_MEMORY.value,
                TaskStoreBackend.SQL.value,
            }:
                raise InvalidTaskStoreError(
                    "Bare task_store strings are only accepted for 'in_memory' and 'sql'"
                )
            return TaskStoreConfig(backend=TaskStoreBackend(config))
        if isinstance(config, dict):
            return TaskStoreConfig(**config)
        raise InvalidTaskStoreError("task_store must be a backend name, config, or store")
