"""A hold left by a run that died is released when the run goes on or ends.

A model call is held at its worst case before it is made and committed at
its real cost after. The budget module promised that a hold left by a
process that died in between is released; nothing released it. On the
steward's server the two runs killed mid-call during P1 and P3 left 5 and 7
cents held on their day's counter for good, and with a daily cap of cents a
single crash would have locked up a fifth of the day. Only one attempt of a
run holds its lease, so what the run held before it went on is stale: a
resumed run releases it before it spends, and a run ended from outside
releases it with its record.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.budgets import BudgetLedger
from test_budget_enforcement import _agent
from test_budget_pause import _two_turns

DAY = {
    "application_id": "acme",
    "application": [{"meter": "model_cost_usd", "limit": 5.0, "window": "day"}],
    "request": [{"meter": "model_calls", "limit": 1}],
}


async def _day_key(agent, run_id: str) -> str:
    budgets = agent._build_run_budgets(run_id=run_id, session_id=None)
    ((_, key, _),) = [entry for entry in budgets.limits("model_cost_usd")]
    return key


async def _left_by_a_dead_attempt(agent, run_id: str) -> str:
    key = await _day_key(agent, run_id)
    ledger = BudgetLedger(agent.memory_router)
    await ledger.reserve(key, "model_cost_usd", 0.07, limit=5.0, run_id=run_id)
    assert (await ledger.reserved(key)).get("model_cost_usd") == pytest.approx(0.07)
    return key


@pytest.mark.asyncio
async def test_a_resumed_run_releases_what_its_dead_attempt_held():
    agent = await _agent(_two_turns(), budgets=DAY)
    paused = await agent.run("go", session_id="stale-1")
    assert paused["status"] == "awaiting_budget"
    key = await _left_by_a_dead_attempt(agent, paused["run_id"])

    await agent.grant_budget(paused["run_id"], amount=2, approver="ops")
    await agent.resume(paused["run_id"])

    assert await BudgetLedger(agent.memory_router).reserved(key) == {}


@pytest.mark.asyncio
async def test_a_run_ended_from_outside_releases_what_it_held():
    agent = await _agent(_two_turns(), budgets=DAY)
    paused = await agent.run("go", session_id="stale-2")
    key = await _left_by_a_dead_attempt(agent, paused["run_id"])

    await agent.abandon_run(paused["run_id"], status="cancelled", reason="cancelled")

    assert await BudgetLedger(agent.memory_router).reserved(key) == {}


@pytest.mark.asyncio
async def test_another_runs_hold_is_left_alone():
    agent = await _agent(_two_turns(runs=2), budgets=DAY)
    first = await agent.run("go", session_id="stale-3")
    second = await agent.run("go", session_id="stale-4")
    key = await _left_by_a_dead_attempt(agent, second["run_id"])

    await agent.abandon_run(first["run_id"], status="cancelled", reason="cancelled")

    assert (await BudgetLedger(agent.memory_router).reserved(key)).get("model_cost_usd") == pytest.approx(0.07)
