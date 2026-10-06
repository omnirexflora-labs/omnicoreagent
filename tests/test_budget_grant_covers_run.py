"""0.5.1 B4: a budget top-up is granted to the run, not to one call.

The 0.5.0 known issue: with a limit as low as one tool call, each call paused
on its own and a person granted each one in turn.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from test_budget_enforcement import PricedModel, _agent, _request_spent


def _call(n: int) -> ModelTurn:
    return ModelTurn(tool_calls=(ToolRequest(f"call_{n}", "lookup", '{"key": "a"}'),))


ONE_TOOL_CALL = {"request": [{"meter": "tool_calls", "limit": 1}]}


@pytest.mark.asyncio
async def test_one_grant_covers_the_calls_it_pays_for_in_sequence():
    agent = await _agent(PricedModel(_call(1), _call(2), _call(3), _call(4)), budgets=ONE_TOOL_CALL)
    waiting = await agent.run("go", session_id="grant-seq")
    assert waiting["status"] == "awaiting_budget"

    await agent.grant_budget(waiting["run_id"], amount=3, approver="ops@example.com")
    finished = await agent.resume(waiting["run_id"])

    assert finished["status"] == "success", finished
    assert (await _request_spent(agent, waiting["run_id"]))["tool_calls"] == 4


@pytest.mark.asyncio
async def test_a_grant_without_an_amount_is_only_the_shortfall_of_the_call_that_stopped():
    # The cause of the 0.5.0 known issue. Nothing is per call in the ledger: a
    # grant raises the run's limit, and later calls draw on it. A grant that
    # names no amount is sized to the one call that stopped, and a run that
    # stops one call at a time asks again for each. Naming an amount (above)
    # is how a person pays for the rest of the work.
    agent = await _agent(PricedModel(_call(1), _call(2), _call(3), _call(4)), budgets=ONE_TOOL_CALL)
    result = await agent.run("go", session_id="grant-default")
    pauses = 0
    while result["status"] == "awaiting_budget":
        pauses += 1
        assert pauses < 10
        assert result["budget_request"]["shortfall"] == 1
        await agent.grant_budget(result["run_id"], approver="ops@example.com")
        result = await agent.resume(result["run_id"])

    assert pauses == 3 and result["status"] == "success"


@pytest.mark.asyncio
async def test_one_grant_covers_the_calls_of_one_turn():
    turn = ModelTurn(
        tool_calls=tuple(ToolRequest(f"call_{n}", "lookup", '{"key": "a"}') for n in range(4))
    )
    agent = await _agent(PricedModel(turn), budgets=ONE_TOOL_CALL)
    waiting = await agent.run("go", session_id="grant-par")
    assert waiting["status"] == "awaiting_budget"

    await agent.grant_budget(waiting["run_id"], amount=3, approver="ops@example.com")
    finished = await agent.resume(waiting["run_id"])

    assert finished["status"] == "success", finished
    assert (await _request_spent(agent, waiting["run_id"]))["tool_calls"] == 4
