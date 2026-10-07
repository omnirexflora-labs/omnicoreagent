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
    Reservation,
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


# --- no read, change and write back ------------------------------------------


class _NoRewrite(InMemoryStore):
    """A store that fails the old way: reading a whole counter to change it in
    Python and saving it back is an error here."""

    async def save_budget_state(self, state, expected_version):
        raise AssertionError("a budget change must not rewrite a whole counter")


@pytest.mark.asyncio
async def test_a_budget_change_is_one_atomic_store_call_never_a_rewrite():
    """The support desk ramp (2026-10-07): every change was a versioned rewrite
    of one shared row, and at 100 runs the row conflicted until the retries ran
    out. Replaces the two tests that bounded those retries."""
    ledger = _ledger(_NoRewrite())
    key = "application:acme:total"

    hold = await ledger.reserve(key, "model_cost_usd", 1.0, limit=5.0, run_id="run_1")
    await ledger.commit(hold, actual=0.5, also=[("model_tokens", 10, None)])
    await ledger.charge(key, "model_calls", 1, limit=None)

    assert await ledger.usage(key) == {
        "model_cost_usd": 0.5,
        "model_tokens": 10,
        "model_calls": 1,
    }


# --- a counter written by 0.5.x ----------------------------------------------


def _legacy_row(key: str) -> dict:
    """What 0.5.1 kept for one key: one document, holds inside it."""
    return {
        "key": key,
        "meters": {"model_cost_usd": 2.0, "tool_calls": 7},
        "reservations": {
            "hold_dead": {
                "meter": "model_cost_usd",
                "amount": 1.5,
                "run_id": "run_dead",
                "held_at": "2026-10-01T10:00:00+00:00",
            },
            "hold_alive": {
                "meter": "model_cost_usd",
                "amount": 0.5,
                "run_id": "run_alive",
                "held_at": "2026-10-01T10:00:01+00:00",
            },
        },
        "grants": {"model_cost_usd": 3.0},
        "grant_history": [
            {
                "meter": "model_cost_usd",
                "amount": 3.0,
                "approver": "ada",
                "note": "launch week",
                "granted_at": "2026-10-01T09:00:00+00:00",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_a_counter_written_by_0_5_x_is_moved_on_first_touch(backend, tmp_path):
    """Upgrading must not lose what was spent, what is held, or what was
    granted: the old document is read once, moved, and removed."""
    store = BACKENDS[backend](tmp_path)
    key = f"application:legacy-{backend}-{os.urandom(4).hex()}:total"
    await store.save_budget_state(_legacy_row(key), expected_version=None)
    ledger = _ledger(store)

    assert await ledger.usage(key) == {"model_cost_usd": 2.0, "tool_calls": 7}
    assert await ledger.reserved(key) == {"model_cost_usd": pytest.approx(2.0)}
    assert await ledger.granted(key) == {"model_cost_usd": 3.0}
    assert [e["approver"] for e in await ledger.grant_history(key)] == ["ada"]
    assert {h["id"] for h in await store.list_budget_holds(key)} == {"hold_dead", "hold_alive"}

    # The limit counts the old spend, the old holds and the old grant:
    # 2.0 spent + 2.0 held against a limit of 2.0 plus 3.0 granted leaves 1.0.
    await ledger.charge(key, "model_cost_usd", 1.0, limit=2.0)
    with pytest.raises(BudgetExhausted):
        await ledger.charge(key, "model_cost_usd", 0.5, limit=2.0)

    # The old holds are real holds: a dead run's is released, a live run's settles.
    assert await ledger.release_for_runs(key, run_ids=["run_dead"]) == 1
    assert await ledger.reserved(key) == {"model_cost_usd": pytest.approx(0.5)}
    live = Reservation(key, "model_cost_usd", 0.5, "hold_alive", "run_alive")
    await ledger.commit(live, actual=0.25)
    assert (await ledger.usage(key))["model_cost_usd"] == pytest.approx(3.25)
    assert await ledger.reserved(key) == {}

    # Touched again from another process, nothing is added twice.
    again = _ledger(BACKENDS[backend](tmp_path) if backend != "in_memory" else store)
    assert (await again.usage(key))["model_cost_usd"] == pytest.approx(3.25)
    await ledger.delete(key)
