"""A large tool result is offloaded only where the agent may read it back.

Found by the repository steward's P3 kill rehearsal: a large
`get_file_contents` result was offloaded to the workspace, the model was told
to load it with `read_artifact`, and the steward's strict policy — which has
no rule for reading artifacts — refused that call as "Unknown capability
denied by strict policy". The result was lost to the model. The runtime
already never offers `execute` when governance would refuse it; the same
holds for the place it moves a result to.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_run_suspend import _MODEL, RecordingModel

BIG = "line of a large file\n" * 400


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("big_file", description="Return a large file.")
    def big_file() -> str:
        return BIG

    return tools


def _policy(*allow: str) -> dict:
    return {
        "enabled": True,
        "policy": {
            "name": "offload-policy",
            "mode": "strict",
            "rules": {"allow": [{"rule_id": f"allow_{i}", "capability": c} for i, c in enumerate(allow)]},
        },
    }


async def _run(tmp_path, governance):
    model = RecordingModel([("c1", "big_file", "{}")], "done")
    agent = OmniCoreAgent(
        name="offloader",
        system_instruction="Read the file.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "off",
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "tool_offload": {"enabled": True, "threshold_tokens": 50, "threshold_bytes": 500},
            "governance_config": governance,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    result = await agent.run("read it", session_id="offload")
    trajectory = await agent.get_trajectory(result["trace_id"])
    seen = json.dumps(model.calls[-1], default=str)
    return seen, [w["code"] for w in trajectory["harness"]["security_warnings"]]


@pytest.mark.asyncio
async def test_a_result_the_policy_would_not_let_the_agent_read_back_stays_inline(tmp_path):
    seen, warnings = await _run(tmp_path, _policy("tool.local.call"))

    assert "TOOL RESPONSE OFFLOADED" not in seen
    assert seen.count("line of a large file") >= 50, "the model saw the result itself"
    assert "tool_offload_refused_by_policy" in warnings


@pytest.mark.asyncio
async def test_a_result_is_offloaded_where_the_agent_may_read_it(tmp_path):
    seen, warnings = await _run(tmp_path, _policy("tool.local.call", "workspace.artifacts.read"))

    assert "TOOL RESPONSE OFFLOADED" in seen
    assert "tool_offload_refused_by_policy" not in warnings


def test_a_strict_refusal_names_the_capability():
    from omnicoreagent.governance.evaluator import PolicyEvaluator
    from omnicoreagent.governance.models import AuthorityRequest
    from omnicoreagent.governance.policy import policy_from_mapping

    policy = policy_from_mapping(_policy("tool.local.call")["policy"])
    decision = PolicyEvaluator().evaluate(
        policy, AuthorityRequest(capability="workspace.artifacts.read", actor="agent")
    )

    assert decision.effect.value == "deny"
    assert "workspace.artifacts.read" in decision.reason, decision.reason
