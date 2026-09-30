"""A call that ran past its time limit is not said to have failed.

The 0.5.0rc5 gate: a synchronous tool that charged a card after its time
limit was reported as "Tool execution timed out"; the model answered "Charge
failed due to a timeout", and the card was charged. A tool in a thread cannot
be stopped: the model is told its outcome is unknown.
"""

from __future__ import annotations

import json
import time

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


@pytest.mark.asyncio
async def test_the_model_is_told_a_timed_out_call_may_still_take_effect(tmp_path):
    ledger = tmp_path / "ledger"
    tools = ToolRegistry()

    @tools.register_tool("charge", description="Charges the card.")
    def charge(amount: int) -> dict:
        time.sleep(3.5)
        ledger.write_text(f"charged {amount}")
        return {"status": "success"}

    model = RecordingModel([("c1", "charge", '{"amount": 10}')], "done")
    agent = OmniCoreAgent(
        name="payer", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "tool_call_timeout": 2},
    )
    await agent.initialize()
    agent.llm_connection = model

    await agent.run("charge 10", session_id="late")

    told = next(m for m in model.calls[-1] if m.get("tool_call_id") == "c1")
    message = json.loads(told["content"])["message"]
    assert "may still take effect" in message and "Check before calling it again" in message, message
    time.sleep(3)
    assert ledger.read_text() == "charged 10", "the call did finish, after the limit"
    await agent.cleanup()
