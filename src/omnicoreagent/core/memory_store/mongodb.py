from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import errors, IndexModel
from datetime import datetime
import uuid
import asyncio

from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.utils import logger, utc_now_str
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,
)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig
from typing import Callable, Any


class MongoDb(AbstractMemoryStore):
    def __init__(self, uri: str, db_name: str, collection: str):
        self.uri = uri
        self.db_name = db_name
        self.collection_name = collection
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.collection = None
        self._initialized = False
        self.memory_config: dict[str, Any] = {}
        self.summary_config: dict[str, Any] = {}
        self.summarize_fn: Callable = None

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

            message_indexes = [
                IndexModel([("session_id", 1), ("msg_metadata.agent_name", 1)]),
                IndexModel([("session_id", 1)]),
                IndexModel([("msg_metadata.agent_name", 1)]),
                IndexModel([("timestamp", 1)]),
                IndexModel([("status", 1)]),
            ]
            await self.collection.create_indexes(message_indexes)

            self._initialized = True
            logger.debug("Connected to MongoDB")

        except errors.ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise RuntimeError(f"Could not connect to MongoDB at {self.uri}.")

    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )
        self.memory_config = {"mode": mode, "value": value}
        if summary_config:
            self.summary_config = SummaryConfig(**summary_config)
        if summarize_fn:
            self.summarize_fn = summarize_fn

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
                "id": str(uuid.uuid4()),
                "role": role,
                "content": content,
                "msg_metadata": metadata,
                "session_id": session_id,
                "timestamp": utc_now_str(),
                "status": "active",
                "inactive_reason": None,
                "summary_id": None,
            }
            await self.collection.insert_one(message)
        except Exception as e:
            logger.error(f"Failed to store message: {e}")

    async def get_messages(self, session_id: str = None, agent_name: str = None):
        try:
            await self._ensure_connected()
            query = {"status": {"$ne": "inactive"}}
            if session_id:
                query["session_id"] = session_id
            if agent_name:
                query["msg_metadata.agent_name"] = agent_name

            cursor = self.collection.find(query, {"_id": 0}).sort("timestamp", 1)
            messages = await cursor.to_list(length=1000)

            result = [
                {
                    "id": m.get("id"),
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

            result, summary_msg, summarized_ids = await apply_summarization_logic(
                messages=result,
                memory_config=self.memory_config,
                summary_config=self.summary_config,
                summarize_fn=self.summarize_fn,
                agent_name=agent_name,
            )

            if summarized_ids and summary_msg:
                summary_id = str(uuid.uuid4())
                summary_msg["id"] = summary_id
                
                summary_msg_doc = {
                    "id": summary_id,
                    "role": summary_msg["role"],
                    "content": summary_msg["content"],
                    "msg_metadata": summary_msg["msg_metadata"],
                    "session_id": session_id,
                    "timestamp": utc_now_str(),
                    "status": "active",
                    "inactive_reason": None,
                    "summary_id": None,
                }

                async def _background_persist_summary():
                    try:
                        await self._ensure_connected()
                        
                        await self.collection.insert_one(summary_msg_doc)

                        await self.mark_messages_summarized(
                            message_ids=summarized_ids,
                            summary_id=summary_id,
                            retention_policy=getattr(
                                self.summary_config.retention_policy,
                                "value",
                                self.summary_config.retention_policy,
                            ),
                        )
                    except Exception as e:
                        logger.error(f"Background MongoDB persistence failed: {e}")

                asyncio.create_task(_background_persist_summary())

            return result

        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []
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

    async def mark_messages_summarized(
        self,
        message_ids: list[str],
        summary_id: str,
        retention_policy: str = "keep",
    ) -> None:
        """Mark messages as summarized (inactive or delete based on policy).

        Args:
            message_ids: List of message IDs to mark as summarized
            summary_id: ID of the summary message that replaces these
            retention_policy: 'keep' to mark inactive, 'delete' to remove
        """
        if not message_ids:
            return

        try:
            await self._ensure_connected()

            if retention_policy == "delete":
                result = await self.collection.delete_many({"id": {"$in": message_ids}})
                logger.debug(f"Deleted {result.deleted_count} summarized messages")
            else:
                result = await self.collection.update_many(
                    {"id": {"$in": message_ids}},
                    {
                        "$set": {
                            "status": "inactive",
                            "inactive_reason": "summarized",
                            "summary_id": summary_id,
                        }
                    },
                )
                logger.debug(
                    f"Marked {result.modified_count} messages as summarized (inactive)"
                )

        except Exception as e:
            logger.error(f"Failed to mark messages as summarized: {e}")

