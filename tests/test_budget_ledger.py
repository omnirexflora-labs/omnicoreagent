"""B1: the budget ledger, kept in the memory store the application chose.

Counters live beside run state, so a budget is shared by every worker and
survives a restart. Spending is reserved before it happens and corrected
after, so a process that dies mid-call leaves a temporary over-count rather
than losing the spend; reservations left by a dead run are released by its
lease. Two workers cannot both spend the last dollar.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from omnicoreagent.core.budgets import (
    BudgetExhausted,
    BudgetLedger,
    BudgetScope,
    budget_key,
    window_key,
)
from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from test_run_state import BACKENDS


def _ledger(store):
    return BudgetLedger(store)


# --- keys and windows --------------------------------------------------------


def test_a_budget_key_names_its_scope_identity_and_window():
    noon = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    assert window_key("total", noon) == "total"
    assert window_key("day", noon) == "2026-09-20"
    assert window_key("month", noon) == "2026-09"
    assert budget_key(BudgetScope.APPLICATION, "acme", "day", noon) == "application:acme:2026-09-20"
    assert budget_key(BudgetScope.REQUEST, "run_1", "total", noon) == "request:run_1:total"
    # A day's budget starts again the next day.
    assert budget_key(BudgetScope.APPLICATION, "acme", "day", noon + timedelta(days=1)) != budget_key(
        BudgetScope.APPLICATION, "acme", "day", noon
    )


# --- the store contract, on every backend ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_every_memory_store_keeps_budget_usage(backend, tmp_path):
    store = BACKENDS[backend](tmp_path)
    ledger = _ledger(store)
    key = f"application:{backend}-{os.urandom(4).hex()}:total"

    await ledger.charge(key, "model_cost_usd", 1.5, limit=10)
    await ledger.charge(key, "model_cost_usd", 2.0, limit=10)
    await ledger.charge(key, "tool_calls", 3, limit=None)
    usage = await ledger.usage(key)

    assert usage["model_cost_usd"] == pytest.approx(3.5)
    assert usage["tool_calls"] == 3
    assert await ledger.usage("application:nothing-spent:total") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_a_charge_over_the_limit_is_refused_and_nothing_is_spent(backend, tmp_path):
    store = BACKENDS[backend](tmp_path)
    ledger = _ledger(store)
    key = f"application:{backend}-{os.urandom(4).hex()}:total"
    await ledger.charge(key, "model_cost_usd", 9.0, limit=10)

    with pytest.raises(BudgetExhausted) as refused:
        await ledger.charge(key, "model_cost_usd", 2.0, limit=10)

    assert refused.value.meter == "model_cost_usd"
    assert refused.value.limit == 10 and refused.value.used == pytest.approx(9.0)
    assert refused.value.requested == pytest.approx(2.0)
    assert (await ledger.usage(key))["model_cost_usd"] == pytest.approx(9.0)


# --- reservations ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reservation_holds_budget_until_the_real_cost_is_known():
    ledger = _ledger(InMemoryStore())
    key = "request:run_1:total"

    reservation = await ledger.reserve(key, "model_cost_usd", 2.0, limit=3.0, run_id="run_1")
    # While it is held, the rest of the budget is what is left.
    with pytest.raises(BudgetExhausted):
        await ledger.charge(key, "model_cost_usd", 1.5, limit=3.0)

    await ledger.commit(reservation, actual=0.4)
    usage = await ledger.usage(key)

    assert usage["model_cost_usd"] == pytest.approx(0.4)
    await ledger.charge(key, "model_cost_usd", 1.5, limit=3.0)  # now it fits


@pytest.mark.asyncio
async def test_a_released_reservation_costs_nothing():
    ledger = _ledger(InMemoryStore())
    key = "request:run_2:total"

    reservation = await ledger.reserve(key, "model_cost_usd", 2.0, limit=3.0, run_id="run_2")
    await ledger.release(reservation)

    assert await ledger.usage(key) == {}
    assert await ledger.reserved(key) == {}


@pytest.mark.asyncio
async def test_reservations_left_by_a_dead_run_are_released():
    ledger = _ledger(InMemoryStore())
    key = "application:acme:total"
    await ledger.reserve(key, "model_cost_usd", 2.0, limit=10, run_id="run_dead")
    await ledger.reserve(key, "model_cost_usd", 1.0, limit=10, run_id="run_alive")

    released = await ledger.release_for_runs(key, run_ids=["run_dead"])

    assert released == 1
    assert await ledger.reserved(key) == {"model_cost_usd": pytest.approx(1.0)}


@pytest.mark.asyncio
async def test_a_crash_between_spending_and_counting_over_counts_never_under_counts():
    ledger = _ledger(InMemoryStore())
    key = "request:run_3:total"

    # The process dies after reserving and before committing.
    await ledger.reserve(key, "model_cost_usd", 2.0, limit=5.0, run_id="run_3")

    assert await ledger.reserved(key) == {"model_cost_usd": pytest.approx(2.0)}
    assert await ledger.available(key, "model_cost_usd", limit=5.0) == pytest.approx(3.0)


# --- two workers, one budget -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_two_workers_cannot_both_spend_the_last_dollar(backend, tmp_path):
    store = BACKENDS[backend](tmp_path)
    key = f"application:{backend}-{os.urandom(4).hex()}:total"
    workers = [_ledger(store) for _ in range(6)]

    outcomes = await asyncio.gather(
        *(worker.charge(key, "model_cost_usd", 1.0, limit=3.0) for worker in workers),
        return_exceptions=True,
    )

    spent = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, BudgetExhausted)]
    assert len(spent) == 3 and len(refused) == 3, outcomes
    assert (await _ledger(store).usage(key))["model_cost_usd"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_a_store_without_budget_support_leaves_budgets_off():
    from omnicoreagent.core.memory_store.base import AbstractMemoryStore

    class Minimal(AbstractMemoryStore):
        def set_memory_config(self, mode, value=None, summary_config=None, summarize_fn=None):
            pass

        async def store_message(self, role, content, metadata, session_id):
            pass

        async def get_messages(self, session_id=None, agent_name=None):
            return []

        async def clear_memory(self, session_id=None, agent_name=None):
            pass

        async def mark_messages_summarized(self, message_ids, summary_id, retention_policy="keep"):
            pass

    ledger = _ledger(Minimal())

    # The base class defines the methods, so the first call is what tells us.
    await ledger.charge("application:acme:total", "model_cost_usd", 100.0, limit=1.0)

    assert ledger.enabled is False, "budgets stay off instead of failing the run"
    assert await ledger.usage("application:acme:total") == {}


# --- contention: another worker keeps getting there first ------------------------


class _Contended(InMemoryStore):
    """A store where the first ``conflicts`` saves lose the race, as they would
    with another process charging the same key at the same moment."""

    def __init__(self, conflicts: int):
        super().__init__()
        self.conflicts = conflicts
        self.lost = 0

    async def save_budget_state(self, state, expected_version):
        from omnicoreagent.core.runs import RunStateConflict

        if self.lost < self.conflicts:
            self.lost += 1
            raise RunStateConflict("another worker wrote the budget first")
        return await super().save_budget_state(state, expected_version)


@pytest.mark.asyncio
async def test_a_charge_outlasts_a_burst_of_conflicts():
    """Found by P4 of the proving plan, two processes on one Postgres key:
    a charge that lost the race eight times in a row was given up on, and
    a run's model call fails when its charge does. A charge waits out the
    burst instead."""
    store = _Contended(conflicts=20)  # more than the eight tries it used to get
    ledger = _ledger(store)

    await ledger.charge("application:acme:total", "model_calls", 1, limit=None)

    assert store.lost == 20
    assert (await ledger.usage("application:acme:total"))["model_calls"] == 1


@pytest.mark.asyncio
async def test_a_key_that_never_settles_is_still_reported():
    store = _Contended(conflicts=10_000)
    ledger = _ledger(store)
    with pytest.raises(RuntimeError, match="Could not record the budget change"):
        await ledger.charge("application:acme:total", "model_calls", 1, limit=None)
