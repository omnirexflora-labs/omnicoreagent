from typing import Any, Optional
from decouple import config as decouple_config
from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.memory_store.database_memory import DatabaseMemory
from omnicoreagent.core.memory_store.redis_memory import RedisMemoryStore
from omnicoreagent.core.utils import logger
from omnicoreagent.core.utils import normalize_metadata
from omnicoreagent.core.database.mongodb import MongoDb
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.utils import normalize_content


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

    def set_memory_config(self, mode: str, value: int = None) -> None:
        self.memory_store.set_memory_config(mode, value)

    def initialize_memory_store(self):
        if self.memory_store_type == "in_memory":
            self.memory_store = InMemoryStore()
        elif self.memory_store_type == "database":
            db_url = decouple_config("DATABASE_URL", default=None)
            if db_url is None:
                logger.info("Database not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                self.memory_store = DatabaseMemory(db_url=db_url)
        elif self.memory_store_type == "redis":
            redis_url = decouple_config("REDIS_URL", default=None)
            if redis_url is None:
                logger.info("Redis not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                self.memory_store = RedisMemoryStore(redis_url=redis_url)
        elif self.memory_store_type == "mongodb":
            uri = decouple_config("MONGODB_URI", default=None)
            if uri is None:
                logger.info("MongoDB not configured, using in_memory")
                self.memory_store = InMemoryStore()
            else:
                db_name = decouple_config("MONGODB_DB_NAME", default="omnicoreagent")
                collection = decouple_config("MONGODB_COLLECTION", default="messages")
                self.memory_store = MongoDb(
                    uri=uri, db_name=db_name, collection=collection
                )
        else:
            raise ValueError(f"Invalid memory store type: {memory_store_type}")

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

    async def save_message_history_to_file(self, file_path: str) -> None:
        """Save message history to a file.

        Args:
            file_path: Path to save the message history
        """
        try:
            import json

            all_messages = {}
            for session_id in self.memory_store.sessions_history.keys():
                messages = await self.get_messages(session_id)
                if messages:
                    all_messages[session_id] = messages

            with open(file_path, "w") as f:
                json.dump(all_messages, f, indent=2)
            logger.info(f"Message history saved to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save message history: {e}")

    async def load_message_history_from_file(self, file_path: str) -> None:
        """Load message history from a file.

        Args:
            file_path: Path to load the message history from
        """
        try:
            import json

            with open(file_path, "r") as f:
                all_messages = json.load(f)

            for session_id, messages in all_messages.items():
                for message in messages:
                    await self.store_message(
                        role=message["role"],
                        content=message["content"],
                        metadata=message.get("metadata", {}),
                        session_id=session_id,
                    )
            logger.info(f"Message history loaded from {file_path}")
        except Exception as e:
            logger.error(f"Failed to load message history: {e}")
