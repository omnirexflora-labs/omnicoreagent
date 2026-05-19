from typing import Any, Optional, Callable
import os

from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent._optional import load_optional
from omnicoreagent.core.logging import logger
from omnicoreagent.core.memory_store.utils import normalize_metadata
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.memory_store.utils import normalize_content


class MemoryRouter:
    def __init__(self, memory_store_type: str):
        self.memory_store_type = memory_store_type
        self.memory_store: Optional[AbstractMemoryStore] = None
        self.initialize_memory_store()

    def __str__(self):
        """Return a readable string representation of the MemoryRouter."""
        return f"MemoryRouter(type={self.memory_store_type}, store={type(self.memory_store).__name__})"

    def __repr__(self):
        """Return a detailed representation of the MemoryRouter."""
        return self.__str__()

    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        self.memory_store.set_memory_config(mode, value, summary_config, summarize_fn)

    def initialize_memory_store(self):
        if self.memory_store_type == "in_memory":
            self.memory_store = InMemoryStore()
        elif self.memory_store_type == "sql":
            db_url = os.environ.get("DATABASE_URL")
            if db_url is None:
                logger.info("SQL memory not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                DatabaseMessageStore = load_optional(
                    "SQL database memory",
                    "postgres",
                    lambda: __import__(
                        "omnicoreagent.core.memory_store.sql_db_memory",
                        fromlist=["DatabaseMessageStore"],
                    ).DatabaseMessageStore,
                )
                self.memory_store = DatabaseMessageStore(db_url=db_url)
        elif self.memory_store_type == "redis":
            redis_url = os.environ.get("REDIS_URL")
            if redis_url is None:
                logger.info("Redis not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                RedisMemoryStore = load_optional(
                    "Redis memory",
                    "redis",
                    lambda: __import__(
                        "omnicoreagent.core.memory_store.redis_memory",
                        fromlist=["RedisMemoryStore"],
                    ).RedisMemoryStore,
                )
                self.memory_store = RedisMemoryStore(redis_url=redis_url)
        elif self.memory_store_type == "mongodb":
            uri = os.environ.get("MONGODB_URI")
            if uri is None:
                logger.info("MongoDB not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                db_name = os.environ.get("MONGODB_DB_NAME", "omnicoreagent")
                collection = os.environ.get("MONGODB_COLLECTION", "messages")
                MongoDb = load_optional(
                    "MongoDB memory",
                    "mongodb",
                    lambda: __import__(
                        "omnicoreagent.core.memory_store.mongodb",
                        fromlist=["MongoDb"],
                    ).MongoDb,
                )
                self.memory_store = MongoDb(
                    uri=uri, db_name=db_name, collection=collection
                )
        else:
            raise ValueError(
                "Invalid memory store type: "
                f"{self.memory_store_type}. Use one of: in_memory, sql, redis, mongodb"
            )

    def switch_memory_store(self, memory_store_type: str):
        if memory_store_type != self.memory_store_type:
            self.memory_store_type = memory_store_type
            self.initialize_memory_store()
            logger.info(f"Switched memory store to {memory_store_type}")
        else:
            logger.info(f"Memory store already set to {memory_store_type}")

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict,
        session_id: str,
    ) -> None:
        if metadata is None:
            raise ValueError(
                "Metadata cannot be None. Please provide a valid metadata dictionary."
            )
        metadata = normalize_metadata(metadata)
        content = normalize_content(content)

        await self.memory_store.store_message(role, content, metadata, session_id)

    async def get_messages(
        self, session_id: str, agent_name: str = None
    ) -> list[dict[str, Any]]:
        messages = await self.memory_store.get_messages(session_id, agent_name)
        for message in messages:
            message["metadata"] = message.pop("msg_metadata", None)
        return messages

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        await self.memory_store.clear_memory(session_id, agent_name)

    def get_memory_store_info(self) -> dict[str, Any]:
        """Get information about the current memory store."""
        return {
            "type": self.memory_store_type,
            "available": True,
            "store_class": type(self.memory_store).__name__,
        }
