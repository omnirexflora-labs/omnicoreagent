from abc import ABC, abstractmethod
from typing import List, Callable, Any


class AbstractMemoryStore(ABC):
    @abstractmethod
    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict,
        session_id: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_messages(
        self, session_id: str = None, agent_name: str = None
    ) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_messages_summarized(
        self,
        message_ids: list[str],
        summary_id: str,
        retention_policy: str = "keep",
    ) -> None:
        """Mark messages as summarized (inactive or delete based on policy)."""
        raise NotImplementedError

