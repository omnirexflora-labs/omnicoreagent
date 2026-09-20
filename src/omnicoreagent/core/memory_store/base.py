from abc import ABC, abstractmethod
from typing import List, Callable


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

    # --- run state (durable runs) ---------------------------------------
    # Not abstract: a custom store without these keeps working, and its runs
    # are simply not durable.

    async def save_run_state(
        self, record: dict, expected_version: int | None
    ) -> int:
        """Create (``expected_version=None``) or update a run record.

        Returns the new version. Raises ``RunStateConflict`` if the record
        exists on create, or its version is not ``expected_version`` on update.
        """
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def get_run_state(self, run_id: str) -> dict | None:
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def list_run_states(
        self, session_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    # --- budgets ---------------------------------------------------------

    async def get_budget_state(self, key: str) -> dict | None:
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def save_budget_state(self, state: dict, expected_version: int | None) -> int:
        """Create or update a budget counter; the same versioned contract as
        run state (``RunStateConflict`` when another writer got there first)."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

