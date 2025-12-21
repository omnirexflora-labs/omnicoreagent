from typing import Any, Optional
import threading
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.utils import logger, utc_now_str
import copy
import os
import json


class InMemoryStore(AbstractMemoryStore):
    """In memory store - Database compatible version"""

    def __init__(
        self,
    ) -> None:
        """Initialize memory storage.

        Args:
            max_context_tokens: Maximum tokens to keep in memory
            debug: Enable debug logging
        """

        self.sessions_history: dict[str, list[dict[str, Any]]] = {}
        self.memory_config: dict[str, Any] = {}
        self._lock = threading.RLock()

    def set_memory_config(self, mode: str, value: int = None) -> None:
        """Set global memory strategy.

        Args:
            mode: Memory mode ('sliding_window', 'token_budget')
            value: Optional value (e.g., window size or token limit)
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

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict,
        session_id: str,
    ) -> None:
        """Store a message in memory."""
        # Defensive copy to avoid external mutation after storage
        metadata_copy = dict(metadata)

        # Normalize agent_name if present for consistent storage
        if "agent_name" in metadata_copy and isinstance(
            metadata_copy["agent_name"], str
        ):
            metadata_copy["agent_name"] = metadata_copy["agent_name"].strip()

        message = {
            "role": role,
            "content": content,
            "session_id": session_id,
            "timestamp": utc_now_str(),
            "msg_metadata": metadata_copy,
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
            messages = list(self.sessions_history[session_id])

        mode = self.memory_config.get("mode", "token_budget")
        value = self.memory_config.get("value")
        if mode.lower() == "sliding_window":
            messages = messages[-value:]

        elif mode.lower() == "token_budget":
            total_tokens = sum(len(str(msg["content"]).split()) for msg in messages)

            while value is not None and total_tokens > value and messages:
                messages.pop(0)
                total_tokens = sum(len(str(msg["content"]).split()) for msg in messages)

        # If caller supplied an agent_name, normalize compare (strip only)
        if agent_name:
            # normalize caller arg
            agent_name_norm = agent_name.strip()
            filtered = [
                msg
                for msg in messages
                if (msg.get("msg_metadata", {}).get("agent_name") or "").strip()
                == agent_name_norm
            ]
        else:
            filtered = messages
        # Return deep copies so caller cannot change our internal store
        return [copy.deepcopy(m) for m in filtered]

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
                    # Remove only messages for specific agent in this session
                    self.sessions_history[session_id] = [
                        msg
                        for msg in self.sessions_history[session_id]
                        if msg.get("msg_metadata", {}).get("agent_name") != agent_name
                    ]
                else:
                    # Remove entire session
                    del self.sessions_history[session_id]
            elif agent_name:
                # Remove messages for specific agent across all sessions
                for session_id in list(self.sessions_history.keys()):
                    self.sessions_history[session_id] = [
                        msg
                        for msg in self.sessions_history[session_id]
                        if msg.get("msg_metadata", {}).get("agent_name") != agent_name
                    ]
                    # Remove empty sessions
                    if not self.sessions_history[session_id]:
                        del self.sessions_history[session_id]
            else:
                # Clear all memory
                self.sessions_history = {}

        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
