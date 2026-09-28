"""Two things the See and Improve stranger test found in the evidence.

- A run whose model never answered had `totals.estimated_cost_usd` None
  (nothing priced) but `including_subagents.estimated_cost_usd` 0.0, one
  record saying two things. Both say None when nothing was priced.
- A training record's step had `resumed` only when it was a resumed step, so
  `step["resumed"]` raised on every other; every step has it now.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class SilentModel(ScriptedModel):
    async def llm_call(self, messages, tools=None, **kwargs):
        raise RuntimeError("the provider never answered")


async def _agent(tmp_path, monkeypatch, model):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path))
    agent = OmniCoreAgent(
        name="evidence", system_instruction="x", model_config=MODEL,
        agent_config={"guardrail_mode": "off"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


@pytest.mark.asyncio
async def test_nothing_priced_reads_the_same_everywhere(tmp_path, monkeypatch):
    agent = await _agent(tmp_path, monkeypatch, SilentModel())
    result = await agent.run("hi")
    totals = (await agent.get_trajectory(result["trace_id"]))["totals"]
    await agent.cleanup()

    assert totals["estimated_cost_usd"] is None
    assert totals["including_subagents"]["estimated_cost_usd"] is None


@pytest.mark.asyncio
async def test_every_training_step_says_whether_it_was_resumed(tmp_path, monkeypatch):
    agent = await _agent(tmp_path, monkeypatch, ScriptedModel())
    result = await agent.run("hi")
    (record,) = await agent.training_records(run_id=result["run_id"])
    await agent.cleanup()

    assert record["steps"] and all(step["resumed"] is False for step in record["steps"])
