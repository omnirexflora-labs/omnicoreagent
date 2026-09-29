"""G1: an agent is governed unless it says otherwise.

The docs landing said "It acts safely" while an agent built from the
quickstart had no policy (governance_config.enabled defaulted to False; an
outside positioning analysis, 2026-09-29). From 0.5.0 an agent with no
governance settings is governed by the `permissive-dev` profile: nothing pauses
for a person, and what should not happen unasked is refused (raw secrets,
unrestricted host files and network, package installs, shell commands on the
host). `enabled: False` turns it off.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


def _tools():
    tools = ToolRegistry()

    @tools.register_tool("get_weather", description="Weather for a city.")
    def get_weather(city: str) -> dict:
        return {"status": "success", "data": {"city": city, "sky": "clear"}}

    return tools


async def _agent(model, **agent_config):
    agent = OmniCoreAgent(
        name="plain",
        system_instruction="Answer.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **agent_config},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


@pytest.mark.asyncio
async def test_an_agent_with_no_governance_settings_is_governed_by_permissive_dev():
    agent = await _agent(RecordingModel("hi"))
    engine = agent.agent.governance_engine
    assert engine is not None
    assert getattr(engine.policy.profile, "value", engine.policy.profile) == "permissive-dev"
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_quickstart_agent_answers_exactly_as_before():
    model = RecordingModel([("w1", "get_weather", '{"city": "Lagos"}')], "Clear in Lagos.")
    agent = await _agent(model)

    result = await agent.run("Weather in Lagos?", session_id="q")

    assert result["status"] == "success" and result["response"] == "Clear in Lagos."
    tool_result = next(m for m in model.calls[-1] if m.get("tool_call_id") == "w1")
    assert "clear" in json.dumps(tool_result)
    await agent.cleanup()


@pytest.mark.asyncio
async def test_nothing_runs_on_the_host_unasked(tmp_path):
    # Given a host "sandbox" (the local provider, which cannot switch the
    # network off), the default asks a person before that sandbox gets network,
    # and runs nothing meanwhile. Found writing this test: the plan said the
    # default never pauses; it never pauses an agent with no sandbox.
    model = RecordingModel([("c1", "execute", '{"command": "touch made.txt"}')], "done")
    agent = await _agent(
        model,
        governance_config={
            "sandbox_config": {"provider": "local"},
            "sandbox_manifest": {"working_dir": str(tmp_path), "network_policy": {"default": "allow"},
                                 "filesystem_policy": {"default": "allow"}},
        },
    )

    result = await agent.run("Make a file.", session_id="host")

    assert result["status"] == "awaiting_approval"
    approval = (await agent.get_run(result["run_id"]))["approvals"][0]
    assert approval["capability"] == "sandbox.network.configure"
    assert not (tmp_path / "made.txt").exists()
    await agent.cleanup()


def test_the_default_refuses_a_shell_command_on_the_host():
    # And once past that, a command on the host is refused by name.
    from omnicoreagent.governance import build_default_policy
    from omnicoreagent.governance.evaluator import PolicyEvaluator
    from omnicoreagent.sandbox import SandboxCommandSpec
    from omnicoreagent.sandbox.execution import _sandbox_authority_request

    request = _sandbox_authority_request(SandboxCommandSpec(command=["sh", "-c", "touch x"]), "host")
    decision = PolicyEvaluator().evaluate(build_default_policy("permissive-dev"), request)
    assert decision.effect.value == "deny"
    assert "deny_unrestricted_process_exec" in decision.matched_rule_ids


def test_someone_who_enabled_governance_themselves_keeps_interactive_dev():
    # No one's policy loosens: enabling it without a profile meant interactive-dev.
    from omnicoreagent.core.runtime.config import AgentConfig

    assert AgentConfig(governance_config={"enabled": True}).governance_config["profile"] == "interactive-dev"
    assert AgentConfig().governance_config["profile"] == "permissive-dev"
    assert AgentConfig(governance_config={"profile": "strict-production"}).governance_config["profile"] == "strict-production"


@pytest.mark.asyncio
async def test_enabled_false_turns_it_off():
    agent = await _agent(RecordingModel("hi"), governance_config={"enabled": False})
    assert agent.agent.governance_engine is None
    await agent.cleanup()
