from datetime import datetime

import pytest

from omnicoreagent.core.system_prompts import AgentPromptContextBuilder
from omnicoreagent.core.types import Message


class FakeSkillManager:
    def get_skills_context_xml(self):
        return '<skills><skill name="write_tests" /></skills>'


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
        tools_section=(
            "tools_retriever: Discover tools\n"
            "spawn_subagents: Spawn focused workers\n"
            "ls: List workspace files\n"
            "read_file: Read workspace files\n"
            "write_file: Write workspace files\n"
            "read_artifact: Read artifacts\n"
            "read_skill_file: Read skills\n"
            "run_skill_script: Run skill scripts"
        ),
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
    assert '<skill name="write_tests"' in prompt
    assert "[AVAILABLE SUB AGENTS REGISTRY]" in prompt
    assert "researcher" in prompt
    assert "query: str (REQUIRED)" in prompt
    assert "limit: int (optional, default=3)" in prompt
    assert "write_file" in prompt
    assert "workspace tools" in prompt
    assert "offloaded to artifacts" in prompt
    assert "read_artifact" in prompt
    assert "[AVAILABLE TOOLS REGISTRY]\ntools_retriever: Discover tools" in prompt


@pytest.mark.asyncio
async def test_subagent_prompt_dynamic_only_matches_spawn_tool_surface():
    builder = AgentPromptContextBuilder(
        enable_subagents=True,
        is_tool_offload_enabled=lambda: False,
    )

    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        tools_section=(
            "spawn_subagents: Spawn focused workers\n"
            "ls: List workspace files\n"
            "read_file: Read workspace files\n"
            "write_file: Write workspace files"
        ),
        sub_agents=None,
    )

    assert "workers" in prompt
    assert "spawn_subagents" in prompt
    assert "ad hoc workers" in prompt
    assert "native delegate tools" not in prompt
    assert "native delegate tools" not in prompt
    assert "read_file" in prompt
    assert "workspace tools" in prompt
    assert "[AVAILABLE SUB AGENTS REGISTRY]" not in prompt


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
        base_system_prompt="base system",
        tools_section="No tools available",
        sub_agents=None,
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
        enable_subagents=False,
        is_tool_offload_enabled=lambda: False,
    )

    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        tools_section="No tools available",
        sub_agents=[FakeSubAgent()],
    )

    assert "workers" in prompt
    assert "spawn_subagents" not in prompt
    assert "ad hoc workers" not in prompt
    assert "native delegate tools" in prompt
    assert "native delegate tools" in prompt
    assert "matching worker" in prompt
    assert "[AVAILABLE SUB AGENTS REGISTRY]" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_omits_disabled_optional_context():
    builder = AgentPromptContextBuilder(
        is_tool_offload_enabled=lambda: False,
    )

    prompt = await builder.build_system_prompt(
        base_system_prompt="base system",
        tools_section="No tools available",
        sub_agents=None,
    )

    assert prompt == ("base system\n[AVAILABLE TOOLS REGISTRY]\nNo tools available")


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
    assert messages[3].content.startswith("[CURRENT_DATETIME: 2026-05-06 12:30:45 ")
    assert messages[3].content.endswith("\n\nlatest user")


@pytest.mark.asyncio
async def test_render_sub_agents_registry_handles_empty_list():
    builder = AgentPromptContextBuilder(is_tool_offload_enabled=lambda: False)

    assert await builder.render_sub_agents_registry([]) == "No sub-agents available."


def test_available_tool_names_extracts_rendered_registry_entries():
    builder = AgentPromptContextBuilder(is_tool_offload_enabled=lambda: False)

    assert builder.available_tool_names(
        "Available tools:\n\n"
        "read_file: Inspect files\n"
        "  - path: string (required) - Path\n\n"
        "read_artifact: Read artifact\n"
        "No colon in this line"
    ) == {"read_file", "read_artifact"}
