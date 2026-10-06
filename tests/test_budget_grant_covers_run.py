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
