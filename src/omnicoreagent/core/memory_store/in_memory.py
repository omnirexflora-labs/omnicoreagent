from typing import Any, Callable, Coroutine
import threading
import uuid
import asyncio
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.utils import logger, utc_now_str
from omnicoreagent.core.summarizer.tokenizer import count_message_tokens
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,

)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig
import copy


class InMemoryStore(AbstractMemoryStore):
    """In memory store - Database compatible version with lifecycle tracking."""

    def __init__(
        self,
    ) -> None:
        """Initialize memory storage."""
        self.sessions_history: dict[str, list[dict[str, Any]]] = {}
        self.memory_config: dict[str, Any] = {}
        self.summary_config: SummaryConfig = SummaryConfig()
        self.summarize_fn: Callable | None = None
        self._lock = threading.RLock()

    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        """Set global memory strategy.

        Args:
            mode: Memory mode ('sliding_window', 'token_budget')
            value: Optional value (e.g., window size or token limit)
            summary_config: Optional summarization configuration
            summarize_fn: Optional async function to generate summaries
        """
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )

        self.memory_config = {
            "mode": mode,
            "value": value,
        }

        if summary_config:
            self.summary_config = SummaryConfig(**summary_config)

        if summarize_fn:
            self.summarize_fn = summarize_fn

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict,
        session_id: str,
    ) -> None:
        """Store a message in memory."""
        metadata_copy = dict(metadata)

        if "agent_name" in metadata_copy and isinstance(
            metadata_copy["agent_name"], str
        ):
            metadata_copy["agent_name"] = metadata_copy["agent_name"].strip()

        message = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "session_id": session_id,
            "timestamp": utc_now_str(),
            "msg_metadata": metadata_copy,
            "status": "active",
            "inactive_reason": None,
            "summary_id": None,
        }
        with self._lock:
            if session_id not in self.sessions_history:
                self.sessions_history[session_id] = []
            self.sessions_history[session_id].append(message)

    async def get_messages(
        self, session_id: str = None, agent_name: str = None
    ) -> list[dict[str, Any]]:
        session_id = session_id or "default_session"

        with self._lock:
            if session_id not in self.sessions_history:
                self.sessions_history[session_id] = []
            messages = [
                msg for msg in self.sessions_history[session_id]
                if msg.get("status", "active") == "active"
            ]

        if agent_name:
            agent_name_norm = agent_name.strip()
            messages = [
                msg
                for msg in messages
                if (msg.get("msg_metadata", {}).get("agent_name") or "").strip()
                == agent_name_norm
            ]

        messages, summary_msg, summarized_ids = await apply_summarization_logic(
            messages=messages,
            memory_config=self.memory_config,
            summary_config=self.summary_config,
            summarize_fn=self.summarize_fn,
            agent_name=agent_name,
        )

        if summarized_ids and summary_msg:
            summary_id = str(uuid.uuid4())
            summary_msg["id"] = summary_id
            summary_msg["session_id"] = session_id
            summary_msg["status"] = "active"
            summary_msg["timestamp"] = utc_now_str()

            async def _background_persist_summary():
                with self._lock:
                    self.sessions_history[session_id].insert(0, summary_msg)

                await self.mark_messages_summarized(
                    message_ids=summarized_ids,
                    summary_id=summary_id,
                    retention_policy=getattr(
                        self.summary_config.retention_policy,
                        "value",
                        self.summary_config.retention_policy,
                    ),
                )

            asyncio.create_task(_background_persist_summary())

        return [copy.deepcopy(m) for m in messages]

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        """Clear memory for a session or all memory.

        Args:
            session_id: Session ID to clear (if None, clear all)
            agent_name: Optional agent name to filter by
        """
        try:
            if session_id and session_id in self.sessions_history:
                if agent_name:
                    self.sessions_history[session_id] = [
                        msg
                        for msg in self.sessions_history[session_id]
                        if msg.get("msg_metadata", {}).get("agent_name") != agent_name
                    ]
                else:
                    del self.sessions_history[session_id]
            elif agent_name:
                for sess_id in list(self.sessions_history.keys()):
                    self.sessions_history[sess_id] = [
                        msg
                        for msg in self.sessions_history[sess_id]
                        if msg.get("msg_metadata", {}).get("agent_name") != agent_name
                    ]
                    if not self.sessions_history[sess_id]:
                        del self.sessions_history[sess_id]
            else:
                self.sessions_history = {}

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

        message_ids_set = set(message_ids)

        with self._lock:
            for session_id in self.sessions_history:
                if retention_policy == "delete":
                    self.sessions_history[session_id] = [
                        msg for msg in self.sessions_history[session_id]
                        if msg.get("id") not in message_ids_set
                    ]
                else:
                    for msg in self.sessions_history[session_id]:
                        if msg.get("id") in message_ids_set:
                            msg["status"] = "inactive"
                            msg["inactive_reason"] = "summarized"
                            msg["summary_id"] = summary_id

        logger.debug(
            f"{'Deleted' if retention_policy == 'delete' else 'Marked inactive'} "
            f"{len(message_ids)} summarized messages"
        )

