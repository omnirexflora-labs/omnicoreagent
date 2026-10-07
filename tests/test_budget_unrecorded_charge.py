"""A charge for work that already happened never fails the run.

The support desk ramp (2026-10-07): runs failed with "Could not record the
budget change", reported as ``provider_error`` with ``error: null``, and in ten
of them the refund had already been issued. The model call or the tool call was
done; failing the run could not undo it. Now the charge is tried again for a
bounded time, and if the store is still not there it is kept as unrecorded, in
the run's record, its trace and its budget status, and the run goes on. A hold
before a call may still refuse the call: that is the budget doing its job.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core import budgets as budgets_module
from test_budget_enforcement import CALL_COST, PricedModel, _agent, _events

BUDGETS = {
    "application_id": "desk",
    "application": [{"meter": "model_cost_usd", "limit": 5.0, "window": "day"}],
}


def _after_the_call(change: dict) -> bool:
    """The writes a run makes once its model call has answered."""
    return "settle" in change or ("add" in change and "hold" not in change)


class _StoreDownAfterTheCall:
    """Wraps the agent's store: holds work, but every write after a call fails,
    for as long as ``failures`` lasts (None: always)."""

    def __init__(self, store, failures: int | None):
        self._store = store
        self.failures = failures
        self.refused_writes = 0

    def __getattr__(self, name):
        return getattr(self._store, name)

    async def apply_budget_change(self, key, change):
        if _after_the_call(change) and (self.failures is None or self.refused_writes < self.failures):
            self.refused_writes += 1
            raise ConnectionError("the budget store is unreachable")
        return await self._store.apply_budget_change(key, change)


async def _agent_with_flaky_store(failures: int | None):
    agent = await _agent(PricedModel(), budgets=BUDGETS)
    store = agent.memory_router.memory_store
    flaky = _StoreDownAfterTheCall(store, failures)
    agent.memory_router.memory_store = flaky
    return agent, flaky


@pytest.mark.asyncio
async def test_a_charge_that_cannot_be_recorded_does_not_fail_the_run(monkeypatch):
    monkeypatch.setattr(budgets_module, "_UNRECORDED_AFTER_SECONDS", 0.4)
    agent, flaky = await _agent_with_flaky_store(failures=None)

    result = await agent.run("go", session_id="desk-1")

    assert result["status"] == "success", result
    assert result["response"] == "done"
    assert flaky.refused_writes >= 2, "the charge was tried again before it was given up"

    # The record says what the counters cannot.
    record = await agent.get_run(result["run_id"])
    assert record["status"] == "completed"
    unrecorded = record["unrecorded_charges"]
    assert {c["meter"] for c in unrecorded} >= {"model_cost_usd"}
    cost = next(c for c in unrecorded if c["meter"] == "model_cost_usd")
    assert cost["amount"] == pytest.approx(CALL_COST)
    assert cost["scope"] == "application" and cost["key"].startswith("application:desk:")
    assert "unreachable" in cost["error"]

    # The trace says it too.
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    events = _events(trace, "budget_charge_unrecorded")
    assert events and events[0].metadata["charges"][0]["meter"] == "model_cost_usd"

    # And so does the budget status, once the store is back: the hold the dead
    # write left behind was released when the run ended, and nothing is double counted.
    flaky.failures = 0
    status = await agent.budget_status(result["run_id"])
    application = next(e for e in status if e["scope"] == "application")
    assert application["unrecorded"] == pytest.approx(CALL_COST)
    assert application["reserved"] == 0
    assert application["spent"] == 0


@pytest.mark.asyncio
async def test_a_store_that_comes_back_in_time_records_the_charge(monkeypatch):
    monkeypatch.setattr(budgets_module, "_UNRECORDED_AFTER_SECONDS", 10.0)
    monkeypatch.setattr(budgets_module, "_BACKOFF_SECONDS", 0.01)
    agent, flaky = await _agent_with_flaky_store(failures=3)

    result = await agent.run("go", session_id="desk-2")

    assert result["status"] == "success"
    assert flaky.refused_writes == 3
    record = await agent.get_run(result["run_id"])
    assert not record.get("unrecorded_charges")
    status = await agent.budget_status(result["run_id"])
    application = next(e for e in status if e["scope"] == "application")
    assert application["spent"] == pytest.approx(CALL_COST)
    assert application["unrecorded"] == 0
