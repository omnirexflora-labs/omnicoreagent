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

    async def delete_finished_run_states(self, *, before: str, statuses: tuple[str, ...]) -> int:
        """Remove runs in one of ``statuses`` that started before ``before``
        (an ISO 8601 UTC time), from every listing too. Returns how many."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    # --- budgets ---------------------------------------------------------

    async def delete_budget_state(self, key: str) -> None:
        """Remove one budget counter (a finished request's own)."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported("This memory store keeps no budgets")

    async def get_budget_state(self, key: str) -> dict | None:
        """One budget counter as ``{"key", "meters", "reserved", "grants"}``:
        what is spent, held and granted, per meter. A counter that still has the
        0.5.x shape (one JSON document, holds inside it) is moved into the
        current shape the first time its key is touched. None when the key has
        nothing."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def apply_budget_change(self, key: str, change: dict) -> dict:
        """Apply one change to a budget counter atomically: all of it or none.

        The change is described in ``omnicoreagent.core.budgets`` (guarded
        spends, a hold, unguarded spends, settling or releasing a hold,
        releasing a dead run's holds, a grant). A store must not do this as a
        read, a change in Python and a write back: the limit check and the
        increment are one step in the store, so a crowd of runs on one key
        neither passes the limit nor conflicts with itself (the support desk
        ramp, 2026-10-07). Each hold is a record of its own.
        """
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    # A store that can apply the changes of several keys as ONE atomic step
    # sets this and implements ``apply_budget_changes`` and ``get_budget_states``.
    # SQL and the in-memory store do (one transaction, one lock). Redis keeps
    # each key under its own hash tag, so one script cannot span the keys on a
    # cluster, and MongoDB's multi-document transactions need a replica set;
    # both keep the per-key contract, whose round trips are cheap (no thread
    # hop) and each already atomic.
    batches_budget_changes: bool = False

    async def apply_budget_changes(self, changes: list[tuple[str, dict]]) -> list[dict]:
        """Apply ``[(key, change), ...]`` as one atomic step: every change or
        none. Answers one result per change, in order, each shaped like
        ``apply_budget_change``'s; if any change is refused, nothing is applied
        and the first refusal (in the order given) is the one reported. Keys
        are locked in name order so two calls never deadlock each other."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def get_budget_states(self, keys: list[str]) -> dict[str, dict | None]:
        """Several counters from one read: ``{key: get_budget_state(key)}``."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def get_budget_grant_history(self, key: str) -> list[dict]:
        """Who granted what, and when, most recent last."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def list_budget_holds(self, key: str) -> list[dict]:
        """The holds standing on a counter: ``id``, ``meter``, ``amount``,
        ``run_id``, ``held_at``."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

    async def save_budget_state(self, state: dict, expected_version: int | None) -> int:
        """Write a counter in the 0.5.x shape (one versioned document). Nothing
        in the runtime calls this any more: it is how a counter from 0.5.x is
        planted, to be moved into the current shape on first touch."""
        from omnicoreagent.core.runs import RunStateUnsupported

        raise RunStateUnsupported(type(self).__name__)

