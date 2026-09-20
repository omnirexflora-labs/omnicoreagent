"""B4: a budget that runs out waits for a person instead of throwing work away.

A run that cannot afford its next step stops where it is and says what it
needs. Someone with the authority tops it up and the run carries on from the
checkpoint; someone denies it and the run ends cleanly. A top-up does not
change the policy: it is a recorded exception to one budget, with the name of
whoever granted it. Unattended jobs keep the old behaviour with
``on_exhausted: "terminate"``.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.budgets import BudgetLedger, BudgetScope, budget_key
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.governance.hashing import policy_hash
from test_budget_enforcement import CALL_COST, PricedModel, _agent, _events, _usage


def _two_turns(runs: int = 1) -> PricedModel:
    """Answers with a tool call first, so each run needs a second model call."""
    asking = ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),))
    return PricedModel(*([asking] * runs))


ONE_CALL = {"request": [{"meter": "model_calls", "limit": 1}]}


# --- waiting ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_that_runs_out_waits_instead_of_ending():
    agent = await _agent(_two_turns(), budgets=ONE_CALL)

    result = await agent.run("go", session_id="pause-1")

    assert result["status"] == "awaiting_budget"
    request = result["budget_request"]
    assert (request["scope"], request["meter"]) == ("request", "model_calls")
    assert request["shortfall"] == 1 and request["limit"] == 1
    # The work already done is kept: the run is waiting, not failed.
    record = await agent.get_run(result["run_id"])
    assert record["status"] == "awaiting_budget"


@pytest.mark.asyncio
async def test_the_run_says_what_it_is_waiting_for_in_its_trace():
    agent = await _agent(_two_turns(), budgets=ONE_CALL)

    result = await agent.run("go", session_id="pause-2")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    [suspended] = _events(trace, "run_suspended")
    assert suspended.metadata["budget_request"]["meter"] == "model_calls"


# --- topping up ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_top_up_lets_the_run_finish_from_where_it_stopped():
    model = _two_turns()
    agent = await _agent(model, budgets=ONE_CALL)
    waiting = await agent.run("go", session_id="pause-3")
    calls_before = model.calls

    granted = await agent.grant_budget(
        waiting["run_id"], amount=2, approver="ops@example.com", note="finish this one"
    )
    finished = await agent.resume(waiting["run_id"])

    assert granted["amount"] == 2 and granted["approver"] == "ops@example.com"
    assert finished["status"] == "success"
    assert model.calls > calls_before, "the run carried on rather than starting again"
    spent = await _usage(agent, BudgetScope.REQUEST, waiting["run_id"])
    assert spent["model_calls"] == 2


@pytest.mark.asyncio
async def test_a_denied_top_up_ends_the_run_cleanly():
    agent = await _agent(_two_turns(), budgets=ONE_CALL)
    waiting = await agent.run("go", session_id="pause-4")

    await agent.deny_budget(waiting["run_id"], approver="ops@example.com", note="out of money")
    ended = await agent.resume(waiting["run_id"])

    assert ended["termination_reason"] == "budget_exhausted"
    record = await agent.get_run(waiting["run_id"])
    assert record["status"] == "failed"


# --- a top-up is an exception, not a new policy -------------------------------


@pytest.mark.asyncio
async def test_a_top_up_does_not_change_the_policy():
    agent = await _agent(_two_turns(), budgets=ONE_CALL)
    policy = agent.agent.governance_engine.policy
    before = policy_hash(policy)
    waiting = await agent.run("go", session_id="pause-5")

    await agent.grant_budget(waiting["run_id"], amount=1, approver="ops@example.com")

    # The policy still says what it said: the grant is recorded against the
    # budget that ran out, with who granted it.
    assert policy_hash(policy) == before
    assert policy.budgets.limits_for("request")[0].limit == 1
    ledger = BudgetLedger(agent.memory_router)
    key = budget_key(BudgetScope.REQUEST, waiting["run_id"], "total")
    assert await ledger.granted(key) == {"model_calls": 1}
    [audit] = await ledger.grant_history(key)
    assert audit["approver"] == "ops@example.com" and audit["amount"] == 1


@pytest.mark.asyncio
async def test_a_top_up_only_covers_the_budget_that_ran_out():
    agent = await _agent(_two_turns(runs=2), budgets=ONE_CALL)
    first = await agent.run("go", session_id="pause-6")
    await agent.grant_budget(first["run_id"], amount=5, approver="ops@example.com")

    second = await agent.run("go again", session_id="pause-7")

    # A different run has its own budget: the grant did not widen the policy.
    assert second["status"] == "awaiting_budget"


@pytest.mark.asyncio
async def test_an_unattended_job_still_ends_rather_than_waiting():
    agent = await _agent(
        _two_turns(),
        budgets={
            "request": [{"meter": "model_calls", "limit": 1, "on_exhausted": "terminate"}]
        },
    )

    result = await agent.run("go", session_id="pause-8")

    assert result["termination_reason"] == "budget_exhausted"
    assert result["status"] == "error"


# --- what the money bought ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_cost_budget_asks_for_what_the_next_call_could_cost():
    """A cost budget that cannot cover the worst case asks for the difference,
    so a person is told a number they can act on."""
    agent = await _agent(
        PricedModel(),
        budgets={"request": [{"meter": "model_cost_usd", "limit": CALL_COST}]},
    )

    result = await agent.run("go", session_id="pause-9")

    request = result["budget_request"]
    assert request["meter"] == "model_cost_usd"
    assert request["shortfall"] > 0
    assert request["needed"] > CALL_COST


# --- over HTTP ----------------------------------------------------------------


def test_a_waiting_run_is_topped_up_and_resumed_over_http(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from omnicoreagent import OmniServe, OmniServeConfig

    agent = asyncio.run(_agent(_two_turns(), budgets=ONE_CALL))
    server = OmniServe(agent, OmniServeConfig(request_timeout=10))
    with TestClient(server.app) as client:
        waiting = client.post(
            "/run/sync", json={"query": "go", "session_id": "served-budget"}
        ).json()
        assert waiting["status"] == "awaiting_budget"
        assert waiting["budget_request"]["meter"] == "model_calls"

        run = client.get(f"/runs/{waiting['run_id']}")
        assert run.json()["status"] == "awaiting_budget"
        assert run.json()["budget_requests"][0]["status"] == "pending"

        decided = client.post(
            f"/runs/{waiting['run_id']}/budget",
            json={"decision": "grant", "amount": 2, "approver": "ops@example.com"},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "granted"

        resumed = client.post(f"/runs/{waiting['run_id']}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "success"


def test_the_budget_route_reports_a_run_that_is_not_waiting(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from omnicoreagent import OmniServe, OmniServeConfig

    agent = asyncio.run(_agent(PricedModel(), budgets=ONE_CALL))
    server = OmniServe(agent, OmniServeConfig(request_timeout=10))
    with TestClient(server.app) as client:
        finished = client.post(
            "/run/sync", json={"query": "go", "session_id": "served-budget-2"}
        ).json()

        refused = client.post(
            f"/runs/{finished['run_id']}/budget",
            json={"decision": "grant", "approver": "ops@example.com"},
        )

        assert refused.status_code == 404 and "not waiting" in refused.json()["detail"]
