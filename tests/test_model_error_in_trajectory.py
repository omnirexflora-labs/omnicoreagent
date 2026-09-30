"""A model call the provider rejected reads `error` in the trajectory, with why.

The 0.5.0rc5 gate: a call refused for a wrong key read `no_response` with no
error, which the docs define as a call never made; the error event was not
linked to its call, so the trajectory could not pair them.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_execute_tool import _MODEL


class Rejects:
    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        raise PermissionError("AuthenticationError: Incorrect API key provided")


@pytest.mark.asyncio
async def test_a_rejected_model_call_reads_error_with_its_reason():
    agent = OmniCoreAgent(name="k", system_instruction="x", model_config=_MODEL,
                          agent_config={"guardrail_mode": "off", "enable_workspace_files": False})
    await agent.initialize()
    agent.llm_connection = Rejects()

    result = await agent.run("hi", session_id="wrong-key")

    trajectory = await agent.get_trajectory(trace_id=result["trace_id"])
    calls = [c for step in trajectory["steps"] for c in step.get("model_calls") or []]
    assert calls, trajectory["steps"]
    assert calls[-1]["outcome"] == "error", calls[-1]
    assert "Incorrect API key" in str(calls[-1]["error"])
    await agent.cleanup()
