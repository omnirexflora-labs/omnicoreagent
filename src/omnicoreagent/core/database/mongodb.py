from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import errors, IndexModel
from datetime import datetime
from typing import Any, Optional

from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.utils import logger, utc_now_str


class MongoDb(AbstractMemoryStore):
    def __init__(self, uri: str, db_name: str, collection: str):
        self.uri = uri
        self.db_name = db_name
        self.collection_name = collection
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.collection = None
        self._initialized = False
        self.memory_config = {"mode": "token_budget", "value": None}

    async def _ensure_connected(self):
        """Ensure MongoDB connection is established"""
        if self._initialized:
            return

        try:
            collection_name = self.collection_name
            self.client = AsyncIOMotorClient(self.uri)
            await self.client.admin.command("ping")

            self.db = self.client[self.db_name]
            if collection_name is None:
                logger.warning("No collection name provided, using default name")
                collection_name = f"{self.db_name}_collection_name"
            self.collection = self.db[collection_name]
            logger.debug(f"Using collection: {collection_name}")

            # Create indexes for messages collection
            message_indexes = [
                IndexModel([("session_id", 1), ("msg_metadata.agent_name", 1)]),
                IndexModel([("session_id", 1)]),
                IndexModel([("msg_metadata.agent_name", 1)]),
                IndexModel([("timestamp", 1)]),
            ]
            await self.collection.create_indexes(message_indexes)

            self._initialized = True
            logger.debug("Connected to MongoDB")

        except errors.ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise RuntimeError(f"Could not connect to MongoDB at {self.uri}.")

    def set_memory_config(self, mode: str, value: int = None) -> None:
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )
        self.memory_config = {"mode": mode, "value": value}

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str = None,
    ) -> None:
        try:
            await self._ensure_connected()
            if metadata is None:
                metadata = {}
            message = {
                "role": role,
                "content": content,
                "msg_metadata": metadata,
                "session_id": session_id,
                "timestamp": utc_now_str(),
            }
            await self.collection.insert_one(message)
        except Exception as e:
            logger.error(f"Failed to store message: {e}")

    async def get_messages(self, session_id: str = None, agent_name: str = None):
        try:
            await self._ensure_connected()
            query = {}
            if session_id:
                query["session_id"] = session_id
            if agent_name:
                query["msg_metadata.agent_name"] = agent_name

            cursor = self.collection.find(query, {"_id": 0}).sort("timestamp", 1)
            messages = await cursor.to_list(length=1000)

            result = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "session_id": m.get("session_id"),
                    "timestamp": (
                        m["timestamp"].timestamp()
                        if isinstance(m["timestamp"], datetime)
                        else m["timestamp"]
                    ),
                    "msg_metadata": m.get("msg_metadata"),
                }
                for m in messages
            ]

            # Apply memory config
            mode = self.memory_config.get("mode", "token_budget")
            value = self.memory_config.get("value")
            if mode.lower() == "sliding_window" and value is not None:
                result = result[-value:]
            if mode.lower() == "token_budget" and value is not None:
                total_tokens = sum(len(str(msg["content"]).split()) for msg in result)
                while total_tokens > value and result:
                    result.pop(0)
                    total_tokens = sum(
                        len(str(msg["content"]).split()) for msg in result
                    )

        except Exception as e:
            logger.error(f"Failed to retrieve messages: {e}")
            return []

        return result

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        try:
            await self._ensure_connected()
            query = {}
            if session_id:
                query["session_id"] = session_id
            if agent_name:
                query["msg_metadata.agent_name"] = agent_name
            await self.collection.delete_many(query)
        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
