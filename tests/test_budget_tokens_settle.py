"""A call that crosses a token budget is recorded; the next call is stopped.

Found writing the Budgets page (D6): tokens are known only after a call, and
counting them was checked against the limit like a new spend. The call that
crossed a `model_tokens` limit was refused at settlement: its tokens were
never recorded (1773 counted for calls that used about 2674), its dollar hold
was not settled, and resuming made the same call again, paid twice. A spend
that already happened is always recorded; a token budget is checked before
each call instead, against the input the call will send.
"""

from __future__ import annotations

import pytest

from dataclasses import replace

from omnicoreagent.core.budgets import BudgetLedger, BudgetScope, budget_key
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.token_usage import Usage
from test_budget_enforcement import PricedModel, _agent


class LongAnswers(PricedModel):
    """Each call uses 1,000 tokens: more than its input, as a real answer can."""

    async def llm_call(self, messages, tools=None, **kwargs):
        turn = await super().llm_call(messages, tools, **kwargs)
        return replace(turn, usage=Usage(requests=1, request_tokens=300, response_tokens=700, total_tokens=1000))


@pytest.mark.asyncio
async def test_the_call_that_crosses_the_limit_is_recorded_and_the_next_one_waits():
    model = LongAnswers(ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),)))
    agent = await _agent(
        model,
        budgets={
            "session": [
                {"meter": "model_tokens", "limit": 1500},
                {"meter": "model_cost_usd", "limit": 10},
            ]
        },
    )

    first = await agent.run("go", session_id="tokens-1")

    # Both calls fit when they were made (each one's input did), both happened
    # (1,000 tokens each), and both are counted, past the limit.
    assert first["status"] == "success" and model.calls == 2
    ledger = BudgetLedger(agent.memory_router)
    key = budget_key(BudgetScope.SESSION, "tokens-1", "total")
    spent = await ledger.usage(key)
    assert spent["model_tokens"] == 2000
    assert spent["model_cost_usd"] > 0
    assert await ledger.reserved(key) == {}  # no hold left behind

    second = await agent.run("again", session_id="tokens-1")

    # The budget is spent: the next run waits for a person before any call.
    assert second["status"] == "awaiting_budget"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_a_call_whose_input_cannot_fit_is_not_made():
    model = LongAnswers()
    agent = await _agent(model, budgets={"request": [{"meter": "model_tokens", "limit": 50}]})

    result = await agent.run("go", session_id="tokens-2")

    assert result["status"] == "awaiting_budget" and model.calls == 0
