"""Unsafe or surprising configurations are stated in every run's header.

The trace of each run says, in `run_configuration`, when code can run with less
protection than a reader might assume. Each warning is also logged once when
the agent initializes.
"""

from __future__ import annotations

import logging

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_execute_tool import ScriptedModel, _MODEL


def _skill(root):
    skill = root / ".agents" / "skills" / "tool"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: tool\ndescription: A tool.\n---\nRun it.sh.\n")
    (skill / "scripts" / "it.sh").write_text("echo hi\n")


async def _warnings(tmp_path, monkeypatch, caplog, **agent_config):
    monkeypatch.chdir(tmp_path)
    _skill(tmp_path)
    agent = OmniCoreAgent(
        name="warned",
        system_instruction="Hi.",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **agent_config},
        telemetry_config={"capture": "full"},
    )
    with caplog.at_level(logging.WARNING):
        await agent.initialize()
    agent.llm_connection = ScriptedModel("done")
    result = await agent.run("go", session_id="warnings")
    trajectory = await agent.get_trajectory(result["trace_id"])
    return [w["code"] for w in trajectory["harness"]["security_warnings"]], caplog.text


@pytest.mark.asyncio
async def test_host_skill_scripts_without_governance_are_warned_about(tmp_path, monkeypatch, caplog):
    codes, logged = await _warnings(tmp_path, monkeypatch, caplog, enable_agent_skills=True)

    assert codes == ["ungoverned_host_scripts"]
    assert "no policy" in logged


@pytest.mark.asyncio
async def test_a_sandbox_configured_without_governance_is_warned_about(tmp_path, monkeypatch, caplog):
    codes, _ = await _warnings(
        tmp_path,
        monkeypatch,
        caplog,
        governance_config={"enabled": False, "sandbox_config": {"provider": "docker"}},
    )

    assert codes == ["sandbox_unused_without_governance"]


@pytest.mark.asyncio
async def test_governed_skill_scripts_without_a_sandbox_are_noted_as_not_contained(tmp_path, monkeypatch, caplog):
    codes, _ = await _warnings(
        tmp_path,
        monkeypatch,
        caplog,
        enable_agent_skills=True,
        governance_config={"enabled": True, "profile": "interactive-dev"},
    )

    assert codes == ["host_scripts_not_contained"]


@pytest.mark.asyncio
async def test_a_plain_agent_has_no_warnings(tmp_path, monkeypatch, caplog):
    codes, _ = await _warnings(tmp_path, monkeypatch, caplog)

    assert codes == []
