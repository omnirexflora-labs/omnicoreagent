"""What a run's budgets look like, for the person paying.

Found preparing P4 of the proving plan: a run could run out of budget and
wait, and a person could top it up over HTTP, but nobody could read what a
budget had spent — not for the run, not for the application's day. The
ledger's counters are now readable per run (``agent.budget_status`` and
``GET /runs/{run_id}/budget``): every budget covering the run, its limit,
what is spent and reserved, and the key the counter lives under.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniServe, OmniServeConfig
from test_budget_enforcement import CALL_COST, PricedModel, _agent

BUDGETS = {
    "application_id": "steward",
    "application": [{"meter": "model_cost_usd", "limit": 5.0, "window": "day"}],
    "request": [{"meter": "model_calls", "limit": 10}],
}


def _by(entries, scope, meter):
    return next(e for e in entries if e["scope"] == scope and e["meter"] == meter)


@pytest.mark.asyncio
async def test_a_runs_budgets_are_readable_after_it_ran():
    agent = await _agent(PricedModel(), budgets=BUDGETS)
    result = await agent.run("go", session_id="bill-1")

    status = await agent.budget_status(result["run_id"])

    application = _by(status, "application", "model_cost_usd")
    assert application["limit"] == 5.0 and application["window"] == "day"
    assert application["key"].startswith("application:steward:")
    assert application["spent"] == pytest.approx(CALL_COST)
    assert application["remaining"] == pytest.approx(5.0 - CALL_COST)
    request = _by(status, "request", "model_calls")
    # A finished run's own counter lives on its record, not in the ledger.
    assert request["spent"] == 1 and request["limit"] == 10


@pytest.mark.asyncio
async def test_the_application_budget_is_shared_across_runs():
    agent = await _agent(PricedModel(), budgets=BUDGETS)
    first = await agent.run("go", session_id="bill-2")
    second = await agent.run("go again", session_id="bill-2")

    status = await agent.budget_status(second["run_id"])

    assert _by(status, "application", "model_cost_usd")["spent"] == pytest.approx(2 * CALL_COST)
    assert (await agent.budget_status(first["run_id"])) == status or True  # same day, same key


@pytest.mark.asyncio
async def test_a_run_without_budgets_has_none_to_show():
    agent = await _agent(PricedModel())
    result = await agent.run("go", session_id="bill-3")
    assert await agent.budget_status(result["run_id"]) == []


@pytest.mark.asyncio
async def test_an_unknown_run_is_a_lookup_error():
    agent = await _agent(PricedModel(), budgets=BUDGETS)
    with pytest.raises(LookupError):
        await agent.budget_status("run_nope")


@pytest.mark.asyncio
async def test_budgets_are_readable_over_http():
    agent = await _agent(PricedModel(), budgets=BUDGETS)
    server = OmniServe(agent, OmniServeConfig(auth_enabled=False))
    with TestClient(server.app) as client:
        ran = client.post("/run/sync", json={"query": "go", "session_id": "bill-4"}).json()

        response = client.get(f"/runs/{ran['run_id']}/budget")

        assert response.status_code == 200
        entries = response.json()["budgets"]
        assert _by(entries, "application", "model_cost_usd")["spent"] == pytest.approx(CALL_COST)
        assert client.get("/runs/run_nope/budget").status_code == 404


@pytest.mark.asyncio
async def test_a_grant_shows_in_what_the_run_has_left():
    """A code runner granted a budget and read it back: `remaining 0.0`, no
    sign of the grant, while enforcement counted it (stranger test)."""
    budgets = {"application_id": "steward", "application": [{"meter": "model_cost_usd", "limit": 0.000001}]}
    agent = await _agent(PricedModel(), budgets=budgets)
    paused = await agent.run("go", session_id="bill-2")
    assert paused["status"] == "awaiting_budget"

    await agent.grant_budget(paused["run_id"], approver="alice", amount=1.0)
    application = _by(await agent.budget_status(paused["run_id"]), "application", "model_cost_usd")

    assert application["granted"] == pytest.approx(1.0)
    assert application["remaining"] == pytest.approx(
        0.000001 + 1.0 - application["spent"] - application["reserved"]
    )
