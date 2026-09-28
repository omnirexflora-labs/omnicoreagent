"""A training record carries the run's final answer.

Found writing Outcomes and training records (D8): `final_answer` was always
None. The record looked for the answer at `final["response"]`; the
trajectory keeps it at `final["output"]["response"]`.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


@pytest.mark.asyncio
async def test_the_final_answer_is_in_the_training_record(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path))
    agent = OmniCoreAgent(
        name="trainer",
        system_instruction="x",
        model_config=MODEL,
        agent_config={"guardrail_mode": "off"},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel()
    result = await agent.run("hi")

    (record,) = await agent.training_records(run_id=result["run_id"])

    assert record["final_answer"] == result["response"]
    assert record["final_answer"]
