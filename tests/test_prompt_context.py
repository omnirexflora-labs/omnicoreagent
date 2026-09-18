from datetime import datetime
import pytest
from omnicoreagent.core.system_prompts import AgentPromptContextBuilder
from omnicoreagent.core.types import Message


class FakeSkillManager:
    def get_skills_context(self):
        return "- name: write_tests\n  description: Write tests\n  instructions: SKILL.md"


class FakeSubAgent:
    name = "researcher"
    system_instruction = "Research hard problems."

    async def run(self, query: str, limit: int = 3):
        return {"response": query, "limit": limit}


@pytest.mark.asyncio
async def test_build_system_prompt_includes_enabled_harness_context():
    builder = AgentPromptContextBuilder(
        enable_advanced_tool_use=True,
        enable_subagents=True,
        enable_workspace_files=True,
        enable_agent_skills=True,
        is_tool_offload_enabled=lambda: True,
        skill_manager=FakeSkillManager(),
    )
    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        available_tools={
            "read_file",
            "ls",
            "write_file",
            "read_artifact",
            "run_skill_script",
            "spawn_subagents",
            "read_skill_file",
            "tools_retriever",
        },
        sub_agents=[FakeSubAgent()],
    )
    assert prompt.startswith("base system\n")
    assert "workers" in prompt
    assert "spawn_subagents" in prompt
    assert "subagents as an array" in prompt
    assert "ad hoc workers" in prompt
    assert "native delegate tools" in prompt
    assert 'extension name="sub_agents_extension"' not in prompt
    assert 'extension name="dynamic_subagents_extension"' not in prompt
    assert "OBSERVATION RESULT FROM TOOL CALLS" not in prompt
    assert "<observation_marker>" not in prompt
    assert "<tool_call_1>" not in prompt
    assert "args?: dict" not in prompt
    assert "[AVAILABLE SKILLS]" in prompt
    assert "- name: write_tests" in prompt
    assert "write_file" in prompt
    assert "workspace tools" in prompt
    assert "offloaded to artifacts" in prompt
    assert "read_artifact" in prompt


@pytest.mark.asyncio
async def test_subagent_prompt_dynamic_only_matches_spawn_tool_surface():
    builder = AgentPromptContextBuilder(
        enable_subagents=True, is_tool_offload_enabled=lambda: False
    )
    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        available_tools={"ls", "spawn_subagents", "write_file", "read_file"},
        sub_agents=None,
    )
    assert "workers" in prompt
    assert "spawn_subagents" in prompt
    assert "ad hoc workers" in prompt
    assert "native delegate tools" not in prompt
    assert "native delegate tools" not in prompt
    assert "read_file" in prompt
    assert "workspace tools" in prompt


@pytest.mark.asyncio
async def test_extension_prompts_require_backing_tools_in_tools_section():
    builder = AgentPromptContextBuilder(
        enable_advanced_tool_use=True,
        enable_subagents=True,
        enable_workspace_files=True,
        enable_agent_skills=True,
        is_tool_offload_enabled=lambda: True,
        skill_manager=FakeSkillManager(),
    )
    prompt = await builder.build_system_prompt(
        base_system_prompt="base system", available_tools=set(), sub_agents=None
    )
    assert "Use tools_retriever" not in prompt
    assert "workers" not in prompt
    assert "Use workspace tools" not in prompt
    assert "Large tool results" not in prompt
    assert "Consult the available skill catalog" not in prompt
    assert "[AVAILABLE SKILLS]" not in prompt


@pytest.mark.asyncio
async def test_subagent_prompt_configured_only_does_not_claim_spawn_tool():
    builder = AgentPromptContextBuilder(
        enable_subagents=False, is_tool_offload_enabled=lambda: False
    )
    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        available_tools=set(),
        sub_agents=[FakeSubAgent()],
    )
    assert "workers" in prompt
    assert "spawn_subagents" not in prompt
    assert "ad hoc workers" not in prompt
    assert "native delegate tools" in prompt
    assert "native delegate tools" in prompt
    assert "matching worker" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_omits_disabled_optional_context():
    builder = AgentPromptContextBuilder(is_tool_offload_enabled=lambda: False)
    prompt = await builder.build_system_prompt(
        base_system_prompt="base system", available_tools=set(), sub_agents=None
    )
    assert prompt == "base system"


def test_inject_current_datetime_updates_latest_user_message_only():
    builder = AgentPromptContextBuilder(
        is_tool_offload_enabled=lambda: False,
        clock=lambda: datetime(2026, 5, 6, 12, 30, 45),
    )
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="old user"),
        Message(role="assistant", content="assistant"),
        Message(role="user", content="latest user"),
    ]
    builder.inject_current_datetime(messages)
    assert messages[1].content == "old user"
    assert messages[3].content.startswith("[CURRENT_DATETIME: 2026-05-06 12:30:45 UTC]")
    assert messages[3].content.endswith("\n\nlatest user")


def test_injected_datetime_is_always_labelled_utc():
    from datetime import timedelta, timezone

    lagos = timezone(timedelta(hours=1))
    builder = AgentPromptContextBuilder(
        is_tool_offload_enabled=lambda: False,
        clock=lambda: datetime(2026, 5, 6, 13, 30, 45, tzinfo=lagos),
    )
    messages = [Message(role="user", content="hi")]
    builder.inject_current_datetime(messages)
    assert messages[0].content == "[CURRENT_DATETIME: 2026-05-06 12:30:45 UTC]\n\nhi"

    # The default clock reads UTC, not the server's local time.
    default = AgentPromptContextBuilder(is_tool_offload_enabled=lambda: False)
    assert default.clock().utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_extension_aliases_do_not_rewrite_user_task_content():
    builder = AgentPromptContextBuilder(
        enable_advanced_tool_use=True, is_tool_offload_enabled=lambda: False
    )
    prompt = await builder.build_system_prompt(
        base_system_prompt="Discuss tools_retriever as task data.",
        available_tools={"tools_retriever"},
        tool_aliases={"tools_retriever": "tools_retriever_aliased"},
    )
    assert prompt.startswith("Discuss tools_retriever as task data.")
    assert "Use tools_retriever_aliased to search" in prompt
