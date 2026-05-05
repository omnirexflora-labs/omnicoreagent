"""
Memory Store Package

This package provides different memory storage backends:
- InMemoryStore: Simple in-memory storage
- RedisMemoryStore: Redis-backed storage
- DatabaseMemory: SQL database storage
- MongoDBMemory: MongoDB storage
- MemoryRouter: Routes to appropriate backend
"""

from .base import AbstractMemoryStore
from .in_memory import InMemoryStore
from .memory_router import MemoryRouter

__all__ = [
    "AbstractMemoryStore",
    "InMemoryStore",
    "RedisMemoryStore",
    "DatabaseMessageStore",
    "MongoDb",
    "MemoryRouter",
]

_OPTIONAL_EXPORTS = {
    "RedisMemoryStore": ("omnicoreagent.core.memory_store.redis_memory", "redis"),
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store.sql_db_memory", "postgres"),
    "MongoDb": ("omnicoreagent.core.memory_store.mongodb", "mongodb"),
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from omnicoreagent._optional import load_optional

    module_name, extra = _OPTIONAL_EXPORTS[name]
    return load_optional(
        name,
        extra,
        lambda: getattr(__import__(module_name, fromlist=[name]), name),
    )
