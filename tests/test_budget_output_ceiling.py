"""A budgeted model call cannot answer past what its hold paid for.

A hold prices the output at ``max_tokens``. Without one, 4,096 output tokens
were priced and nothing was sent to the provider, so a longer answer settled
above the hold (found in the docs review, 2026-09-25). When a budget governs
the call and no ``max_tokens`` is set, the priced ceiling is now sent too.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.budgets import DEFAULT_ASSUMED_OUTPUT_TOKENS
from omnicoreagent.core.llm import LLMConnection, OUTPUT_TOKEN_CEILING
from omnicoreagent.core.model_protocol import ModelTurn

from test_budget_enforcement import _MODEL, PricedModel, _agent

BUDGETS = {"request": [{"meter": "model_cost_usd", "limit": 10.0}]}


class CeilingSeen(PricedModel):
    def __init__(self, llm_config):
        super().__init__(ModelTurn(content="done"))
        self.llm_config = llm_config
        self.ceilings = []

    async def llm_call(self, messages, tools=None, **kwargs):
        self.ceilings.append(OUTPUT_TOKEN_CEILING.get())
        return await super().llm_call(messages, tools, **kwargs)


def _without_max_tokens():
    return {key: value for key, value in _MODEL.items() if key != "max_tokens"}


@pytest.mark.asyncio
async def test_a_budgeted_call_without_max_tokens_is_capped_at_what_was_priced():
    model = CeilingSeen(_without_max_tokens())
    agent = await _agent(model, budgets=BUDGETS)
    await agent.run("go")
    assert model.ceilings == [DEFAULT_ASSUMED_OUTPUT_TOKENS]


@pytest.mark.asyncio
async def test_max_tokens_of_your_own_is_the_ceiling_and_nothing_is_added():
    model = CeilingSeen(dict(_MODEL))
    agent = await _agent(model, budgets=BUDGETS)
    await agent.run("go")
    assert model.ceilings == [None]


@pytest.mark.asyncio
async def test_without_a_budget_the_provider_decides_as_before():
    model = CeilingSeen(_without_max_tokens())
    agent = await _agent(model, budgets=None)
    await agent.run("go")
    assert model.ceilings == [None]


@pytest.mark.parametrize(
    ("provider", "parameter"), [("openai", "max_completion_tokens"), ("anthropic", "max_tokens")]
)
def test_the_ceiling_reaches_the_provider_request(provider, parameter):
    connection = LLMConnection({"provider": provider, "model": "m", "api_key": "k"})
    messages = [{"role": "user", "content": "hi"}]
    assert parameter not in connection._completion_params(messages)
    token = OUTPUT_TOKEN_CEILING.set(4096)
    try:
        assert connection._completion_params(messages)[parameter] == 4096
    finally:
        OUTPUT_TOKEN_CEILING.reset(token)
