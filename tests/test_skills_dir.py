"""Skills can be found somewhere other than the working directory.

The default root is ``./.agents/skills``, which is right for a project and wrong
for a harness: Harbor puts a trial's skills in a directory of its own
(``/harbor/skills`` unless the task says otherwise), and the working directory
is the task's, which the verifier checks — so copying skills into it would put
files the agent did not make among the ones it is judged on.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.config import AgentConfig
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_execute_tool import _MODEL


def _skill(root, name="lookup"):
    skill = root / name
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Looks things up.\n---\nRun it.sh.\n"
    )
    (skill / "scripts" / "it.sh").write_text("echo hi\n")


async def _skills_found(tmp_path, monkeypatch, **agent_config) -> list[str]:
    work = tmp_path / "task"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)
    agent = OmniCoreAgent(
        name="skilled",
        system_instruction="Hi.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "enable_agent_skills": True,
            **agent_config,
        },
    )
    await agent.initialize()
    try:
        manager = agent.agent.skill_manager
        return sorted(manager.skills) if manager else []
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_skills_are_found_in_the_directory_named(tmp_path, monkeypatch):
    elsewhere = tmp_path / "harbor" / "skills"
    _skill(elsewhere)

    found = await _skills_found(tmp_path, monkeypatch, skills_dir=str(elsewhere))

    assert found == ["lookup"]


@pytest.mark.asyncio
async def test_without_a_directory_named_the_working_directorys_are_used(
    tmp_path, monkeypatch
):
    _skill(tmp_path / "task" / ".agents" / "skills", name="local")

    found = await _skills_found(tmp_path, monkeypatch)

    assert found == ["local"]


def test_the_directory_is_a_setting_of_its_own():
    assert AgentConfig(agent_name="a").skills_dir is None
    assert AgentConfig(agent_name="a", skills_dir="/harbor/skills").skills_dir == (
        "/harbor/skills"
    )
