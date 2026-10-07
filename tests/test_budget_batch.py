"""One call's budget changes, across every scope, in one store call.

A model call on the support desk holds its cost on three counters (request,
session, application) and settles all three afterwards. Each was its own store
call, and on SQL its own transaction and thread hop: 27 of a refund run's 60
transactions (the support desk ramp, 2026-10-07). A store that can do so now
applies all of one call's changes in ONE transaction; these tests are the
contract, and the fallback for a store that cannot.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from omnicoreagent.core.budgets import BudgetExhausted, BudgetLedger
from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from test_budget_hammer import _postgres_store
from test_run_state import _sql_store

# Postgres runs when OMNICOREAGENT_TEST_POSTGRES_URL is set, as in the hammer tests.
BATCHING = {
    "in_memory": lambda tmp_path: InMemoryStore(),
    "sqlite": _sql_store,
    "postgres": _postgres_store,
}


def _hold(meter="model_cost_usd", amount=1.0, limit=None, run_id="run_1"):
    return {
        "hold": {
            "id": f"hold_{uuid4().hex}",
            "meter": meter,
            "amount": amount,
            "run_id": run_id,
            "limit": limit,
            "held_at": "2026-10-07T00:00:00+00:00",
        }
    }


def _keys():
    tag = uuid4().hex[:8]
    return [f"request:r-{tag}:total", f"session:s-{tag}:total", f"application:a-{tag}:day"]


class _NoBatch:
    """A store that offers only the single-key contract."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        if name in ("apply_budget_changes", "get_budget_states", "batches_budget_changes"):
            raise AttributeError(name)
        return getattr(self._store, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(BATCHING))
async def test_all_the_scopes_of_a_call_are_applied_together(backend, tmp_path):
    store = BATCHING[backend](tmp_path)
    ledger = BudgetLedger(store)
    assert ledger.batches
    keys = _keys()
    changes = [(key, {**_hold(limit=10.0), "guard": [["model_calls", 1.0, 5.0]]}) for key in keys]

    results = await ledger.apply_many(changes)

    assert [r["refused"] for r in results] == [None, None, None]
    assert all(r["totals"] == {"model_calls": 1.0} for r in results)
    for key in keys:
        assert await ledger.reserved(key) == {"model_cost_usd": 1.0}
        assert (await ledger.usage(key)) == {"model_calls": 1.0}
        await ledger.delete(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(BATCHING))
async def test_one_scope_refusing_applies_nothing_on_any_scope(backend, tmp_path):
    store = BATCHING[backend](tmp_path)
    ledger = BudgetLedger(store)
    request, session, application = _keys()
    # The session has only half a dollar left, so a one-dollar hold is refused there.
    await ledger.charge(session, "model_cost_usd", 9.5, limit=10.0)
    changes = [(key, _hold(amount=1.0, limit=10.0)) for key in (request, session, application)]

    results = await ledger.apply_many(changes)

    refused = [r["refused"] for r in results]
    assert refused[0] is None and refused[2] is None
    assert refused[1]["meter"] == "model_cost_usd" and refused[1]["used"] == 9.5
    for key in (request, application):
        assert await ledger.reserved(key) == {} and await store.list_budget_holds(key) == []
    assert await store.list_budget_holds(session) == []
    for key in (request, session, application):
        await ledger.delete(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(BATCHING))
async def test_when_two_scopes_would_refuse_the_first_in_call_order_is_reported(backend, tmp_path):
    # The request scope is listed first by the caller; it is the budget a
    # person should hear about, whatever order the store takes its locks in.
    store = BATCHING[backend](tmp_path)
    ledger = BudgetLedger(store)
    request, session, application = _keys()
    for key in (request, application):
        await ledger.charge(key, "tool_calls", 5, limit=5)
    changes = [(key, {"guard": [["tool_calls", 1.0, 5.0]]}) for key in (request, session, application)]

    results = await ledger.apply_many(changes)

    assert results[0]["refused"] is not None
    assert (await ledger.usage(session)) == {}
    for key in (request, session, application):
        await ledger.delete(key)


@pytest.mark.asyncio
async def test_sql_takes_its_row_locks_in_one_fixed_key_order(tmp_path):
    # Two calls that share two counters must not wait on each other in
    # opposite orders (a deadlock): the keys are updated in name order,
    # whatever order the caller listed them in.
    from sqlalchemy import event

    store = _sql_store(tmp_path)
    seen: list[str] = []
    engine = store._sql_manager.get_engine()

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE budget_meters") and isinstance(parameters, (tuple, list)):
            seen.extend(str(p) for p in parameters if str(p).count(":") >= 2)

    ledger = BudgetLedger(store)
    await ledger.apply_many([("session:b:total", {"add": [["tool_calls", 1.0]]}),
                             ("application:a:day", {"add": [["tool_calls", 1.0]]}),
                             ("request:c:total", {"add": [["tool_calls", 1.0]]})])
    updates = [k for k in seen if k in {"session:b:total", "application:a:day", "request:c:total"}]
    first_pass = list(dict.fromkeys(updates))
    assert first_pass == sorted(first_pass), first_pass


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", list(BATCHING))
async def test_a_crowd_of_batched_calls_keeps_every_total_exact(backend, tmp_path):
    store = BATCHING[backend](tmp_path)
    shared = f"application:crowd-{uuid4().hex[:8]}:total"
    limit = 20.0

    async def call(number: int):
        ledger = BudgetLedger(store)
        own = f"request:run-{number}-{uuid4().hex[:6]}:total"
        run = f"run_{number}"
        results = await ledger.apply_many(
            [(own, _hold(amount=1.0, limit=100.0, run_id=run)), (shared, _hold(amount=1.0, limit=limit, run_id=run))]
        )
        await ledger.delete(own)
        return results

    outcomes = await asyncio.gather(*(call(n) for n in range(50)))

    held = [r for r in outcomes if all(x["refused"] is None for x in r)]
    refused = [r for r in outcomes if any(x["refused"] for x in r)]
    assert len(held) == 20 and len(refused) == 30
    assert (await BudgetLedger(store).reserved(shared))["model_cost_usd"] == limit
    assert len(await store.list_budget_holds(shared)) == 20
    await BudgetLedger(store).delete(shared)


@pytest.mark.asyncio
async def test_a_store_without_batching_gets_the_same_answers_one_key_at_a_time(tmp_path):
    ledger = BudgetLedger(_NoBatch(InMemoryStore()))
    assert not ledger.batches
    request, session, application = _keys()
    await ledger.charge(application, "model_cost_usd", 9.5, limit=10.0)

    # A refusal on the last key releases the holds the earlier keys took.
    results = await ledger.apply_many([(key, _hold(amount=1.0, limit=10.0)) for key in (request, session, application)])
    assert results[2]["refused"] is not None
    assert await ledger.reserved(request) == {} and await ledger.reserved(session) == {}

    results = await ledger.apply_many([(key, {**_hold(limit=10.0), "guard": [["model_calls", 1.0, 5.0]]}) for key in (request, session)])
    assert [r["totals"] for r in results] == [{"model_calls": 1.0}] * 2


@pytest.mark.asyncio
async def test_a_refusal_raises_budget_exhausted_through_the_single_key_calls_too(tmp_path):
    ledger = BudgetLedger(InMemoryStore())
    key = _keys()[0]
    with pytest.raises(BudgetExhausted):
        await ledger.charge(key, "tool_calls", 6, limit=5)
