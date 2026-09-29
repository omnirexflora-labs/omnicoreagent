"""R7 (0.5.0rc1 gate): a paused run counts its sub-agents' tokens and cost.

`including_subagents` came only from a segment that ended; a segment that
paused (for an approval, say) after delegating counted its own tokens and
dropped its children's. The gate's governed run reported 10,257 tokens where
the run used 13,301, and the wrong total reached headless trajectory.json and
Harbor. A segment's children are now counted from its trace whether it ended
or paused.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import ScriptedModel, _MODEL


@pytest.mark.asyncio
async def test_a_paused_segments_subagents_are_counted():
    child = OmniCoreAgent(name="researcher", system_instruction="Research.", model_config=_MODEL,
                          agent_config={"guardrail_mode": "off", "enable_workspace_files": False})
    await child.initialize()
    child.llm_connection = ScriptedModel("found it")

    tools = ToolRegistry()

    @tools.register_tool("issue_refund", description="Refund an order.")
    def issue_refund(order_id: str) -> dict:
        return {"status": "success", "data": {"refunded": order_id}}

    policy = build_default_policy("permissive-dev")
    policy.rules.ask.append(PolicyRule(rule_id="ask_refunds", effect=PolicyEffect.ASK,
                                       capability="tool.local.call", target={"tool_name": "issue_refund"}))
    lead = OmniCoreAgent(
        name="lead", system_instruction="Delegate, then refund.", model_config=_MODEL,
        local_tools=tools, sub_agents=[child],
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                      "governance_config": {"policy": policy}},
    )
    await lead.initialize()
    lead.llm_connection = ScriptedModel(
        [("d1", "delegate_researcher", '{"query": "look it up"}')],
        [("r1", "issue_refund", '{"order_id": "1042"}')],
        "done",
    )

    paused = await lead.run("go", session_id="totals")
    assert paused["status"] == "awaiting_approval"
    approval = (await lead.get_run(paused["run_id"]))["approvals"][0]
    await lead.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    await lead.resume(paused["run_id"])

    story = await lead.get_run_trajectory(paused["run_id"])
    lead_tokens = story["totals"]["tokens"]["total"]
    with_children = story["totals"]["including_subagents"]["tokens"]["total"]
    child_tokens = 12  # one call of the scripted child model
    assert with_children == lead_tokens + child_tokens, (lead_tokens, with_children)
    await lead.cleanup()
    await child.cleanup()
