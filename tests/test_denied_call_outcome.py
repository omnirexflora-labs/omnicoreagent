"""A call the policy refused reads `denied` in the run record, as in the trace.

The 0.5.0rc2 gate (a stranger's app): the run record said `error` for a
publish a person denied while the trajectory said `denied`, so the two views
of one run disagreed on what happened to a call.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import ScriptedModel, _MODEL


@pytest.mark.asyncio
async def test_a_refused_call_is_denied_in_the_record_and_the_trace():
    ran: list = []
    tools = ToolRegistry()

    @tools.register_tool("publish_release", description="Publishes a release.")
    def publish_release(version: str) -> dict:
        ran.append(version)
        return {"status": "success", "data": version}

    @tools.register_tool("lookup", description="Looks up a value.")
    def lookup(key: str) -> dict:
        return {"status": "success", "data": 42}

    policy = build_default_policy("permissive-dev")
    policy.rules.deny.insert(0, PolicyRule(
        rule_id="no_publish", effect=PolicyEffect.DENY, capability="tool.local.call",
        target={"tool_name": "publish_release"}))
    agent = OmniCoreAgent(
        name="rel", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                      "governance_config": {"policy": policy}},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(
        [("c1", "publish_release", '{"version": "1.0"}'), ("c2", "lookup", '{"key": "a"}')], "done")

    result = await agent.run("release it", session_id="deny")

    assert ran == []
    run = await agent.get_run(result["run_id"])
    assert {c["tool_call_id"]: c["outcome"] for c in run["tool_calls"]} == {"c1": "denied", "c2": "success"}
    trajectory = await agent.get_run_trajectory(result["run_id"])
    assert trajectory["totals"]["tool_calls"]["by_outcome"]["denied"] == 1
    await agent.cleanup()
