"""Two calls of one turn that ask the same question wait on one approval, and
both run once it is given.

The 0.5.0rc3 gate: two `execute` calls in one turn both needed the sandbox
network. The first paused for approval; the second was refused ("needs
approval") and never replayed, so after the person approved, only one of the
two commands ran. Uses the real Docker backend.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_execute_tool import ScriptedModel, _MODEL, needs_docker

pytestmark = [needs_docker, pytest.mark.asyncio]


async def test_both_calls_run_after_the_one_approval():
    model = ScriptedModel(
        [("e1", "execute", '{"command": "echo one"}'), ("e2", "execute", '{"command": "echo two"}')],
        "done",
    )
    agent = OmniCoreAgent(
        name="parallel", system_instruction="Use execute.", model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off", "enable_workspace_files": False,
            "governance_config": {
                "sandbox_config": {"provider": "docker", "options": {"image": "alpine:3.20"}},
                "sandbox_manifest": {"network_policy": {"default": "allow"}},
            },
        },
    )
    await agent.initialize()
    agent.llm_connection = model

    paused = await agent.run("go", session_id="parallel")
    assert paused["status"] == "awaiting_approval", paused
    (approval,) = (await agent.get_run(paused["run_id"]))["approvals"]
    assert approval["capability"] == "sandbox.network.configure"

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success", result
    run = await agent.get_run(paused["run_id"])
    outcomes = {c["tool_call_id"]: c["outcome"] for c in run["tool_calls"]}
    assert outcomes == {"e1": "success", "e2": "success"}, outcomes
    assert len(run["approvals"]) == 1, "the person was asked once"
    await agent.cleanup()
