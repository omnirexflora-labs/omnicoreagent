"""Budgets under a crowd: many runs, one application budget, no failures.

The support desk ramp (2026-10-07) ran 100 concurrent runs against one
application budget. Every budget change was a read, a change in Python, and a
versioned save of one shared row, so at that load the row conflicted all the
time, the retries ran out, and 46 runs failed with "Could not record the budget
change". Ten of them had already issued a refund. A budget change is now one
atomic operation in the store; these tests are the crowd that broke the old way.

SQLite and in-memory run always; Postgres, Redis and MongoDB run when their
test URLs are set (``OMNICOREAGENT_TEST_POSTGRES_URL``, ``..._REDIS_URL``,
``..._MONGODB_URI``), as in the other store tests.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from omnicoreagent.core.budgets import BudgetExhausted, BudgetLedger
from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from test_run_state import _mongo_store, _redis_store, _sql_store


def _postgres_store(tmp_path):
    url = os.environ.get("OMNICOREAGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PostgreSQL budget tests need OMNICOREAGENT_TEST_POSTGRES_URL")
    from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

    return DatabaseMessageStore(db_url=url)


STORES = {
    "in_memory": lambda tmp_path: InMemoryStore(),
    "sqlite": _sql_store,
    "postgres": _postgres_store,
    "redis": _redis_store,
    "mongodb": _mongo_store,
}

RUNS = 200
# A SQLite commit waits for the disk (about 40 ms here), so its crowd does one
# round; the others do three.
ROUNDS = {"sqlite": 1}
DEFAULT_ROUNDS = 3


async def _one_run(store, key: str, run_number: int, rounds: int) -> None:
    """What a run does to the shared key: a hold, a charge, a release, several times."""
    ledger = BudgetLedger(store)  # one per run, as each run has its own
    run_id = f"run_{run_number}"
    for _ in range(rounds):
        hold = await ledger.reserve(key, "model_cost_usd", 0.5, limit=1e9, run_id=run_id)
        await ledger.commit(hold, actual=0.25, also=[("model_tokens", 100, None)])
        await ledger.charge(key, "tool_calls", 1, limit=1e9)
        spare = await ledger.reserve(key, "model_cost_usd", 0.5, limit=1e9, run_id=run_id)
        await ledger.release(spare)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(STORES))
async def test_two_hundred_runs_share_one_application_budget(backend, tmp_path):
    store = STORES[backend](tmp_path)
    key = f"application:hammer-{backend}-{uuid4().hex[:8]}:total"
    rounds = ROUNDS.get(backend, DEFAULT_ROUNDS)

    outcomes = await asyncio.gather(
        *(_one_run(store, key, number, rounds) for number in range(RUNS)),
        return_exceptions=True,
    )

    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert not failures, f"{len(failures)} of {RUNS} runs failed: {failures[:2]}"
    ledger = BudgetLedger(store)
    usage = await ledger.usage(key)
    # Quarter-dollars are exact in binary, so the sum is exact.
    assert usage["model_cost_usd"] == RUNS * rounds * 0.25
    assert usage["model_tokens"] == RUNS * rounds * 100
    assert usage["tool_calls"] == RUNS * rounds
    assert await ledger.reserved(key) == {}
    assert await store.list_budget_holds(key) == []
    await ledger.delete(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(STORES))
async def test_a_limit_of_n_is_never_passed_by_fifty_concurrent_holds(backend, tmp_path):
    store = STORES[backend](tmp_path)
    key = f"application:limit-{backend}-{uuid4().hex[:8]}:total"
    limit = 20.0

    async def hold(number: int):
        ledger = BudgetLedger(store)
        return await ledger.reserve(
            key, "model_cost_usd", 1.0, limit=limit, run_id=f"run_{number}"
        )

    outcomes = await asyncio.gather(*(hold(n) for n in range(50)), return_exceptions=True)

    held = [o for o in outcomes if not isinstance(o, BaseException)]
    refused = [o for o in outcomes if isinstance(o, BudgetExhausted)]
    others = [o for o in outcomes if isinstance(o, BaseException) and o not in refused]
    assert not others, others[:2]
    assert len(held) == 20 and len(refused) == 30
    ledger = BudgetLedger(store)
    assert (await ledger.reserved(key))["model_cost_usd"] == limit
    assert len(await store.list_budget_holds(key)) == 20

    # Settling what was held spends exactly what was held, and no more.
    await asyncio.gather(*(BudgetLedger(store).commit(h, actual=1.0) for h in held))
    assert (await ledger.usage(key))["model_cost_usd"] == limit
    assert await ledger.reserved(key) == {}
    await ledger.delete(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(STORES))
async def test_fifty_concurrent_charges_never_pass_the_limit(backend, tmp_path):
    store = STORES[backend](tmp_path)
    key = f"application:charges-{backend}-{uuid4().hex[:8]}:total"

    outcomes = await asyncio.gather(
        *(BudgetLedger(store).charge(key, "tool_calls", 1, limit=12) for _ in range(50)),
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, BudgetExhausted)]
    others = [o for o in outcomes if isinstance(o, BaseException) and o not in refused]
    assert not others, others[:2]
    assert len(refused) == 38
    assert (await BudgetLedger(store).usage(key))["tool_calls"] == 12
    await BudgetLedger(store).delete(key)
