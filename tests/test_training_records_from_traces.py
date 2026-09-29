"""R8 (0.5.0rc1 gate): a resumed run is one training record even without its run record.

The docs say that when a run's record is gone (pruned, or kept in another
process's memory), its training record still comes from its traces. For a run
that paused and resumed it came back empty: the run was marked read before its
paused segment was skipped as unfinished, so its completed segment was skipped
too; and even then it would have been built from one segment. It is now built
from every trace of the run, if the last one finished.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import ScriptedModel, _MODEL


def _agent(tmp_path, model=None):
    tools = ToolRegistry()

    @tools.register_tool("issue_refund", description="Refund an order.")
    def issue_refund(order_id: str) -> dict:
        return {"status": "success", "data": {"refunded": order_id}}

    policy = build_default_policy("permissive-dev")
    policy.rules.ask.append(PolicyRule(rule_id="ask_refunds", effect=PolicyEffect.ASK,
                                       capability="tool.local.call", target={"tool_name": "issue_refund"}))
    agent = OmniCoreAgent(
        name="desk", system_instruction="Refund.", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                      "governance_config": {"policy": policy},
                      "workspace_config": {"workspace_dir": str(tmp_path / "workspace")}},
        telemetry_config={"capture": "full"},
    )
    return agent


@pytest.mark.asyncio
async def test_a_resumed_run_is_one_record_from_its_traces_alone(tmp_path):
    agent = _agent(tmp_path)
    await agent.initialize()
    agent.llm_connection = ScriptedModel([("r1", "issue_refund", '{"order_id": "1042"}')], "Refunded.")
    paused = await agent.run("Refund 1042.", session_id="train")
    approval = (await agent.get_run(paused["run_id"]))["approvals"][0]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    await agent.resume(paused["run_id"])
    with_record = await agent.training_records(run_id=paused["run_id"])
    await agent.cleanup()

    # Another agent over the same workspace: the traces are there, the run's
    # record (kept in the first agent's memory) is not.
    reader = _agent(tmp_path)
    await reader.initialize()
    assert await reader.get_run(paused["run_id"]) is None
    from_traces = await reader.training_records(run_id=paused["run_id"])

    assert len(with_record) == 1 and len(from_traces) == 1
    assert len(from_traces[0]["steps"]) == len(with_record[0]["steps"])
    await reader.cleanup()
