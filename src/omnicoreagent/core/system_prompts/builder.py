"""Prompt builders and runtime prompt context assembly."""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from omnicoreagent.core.types import Message
from omnicoreagent.core.system_prompts.extensions import (
    agent_skills_additional_prompt,
    artifact_tool_additional_prompt,
    build_subagents_additional_prompt,
    tools_retriever_additional_prompt,
    workspace_files_additional_prompt,
)


class OmniCoreAgentPromptBuilder:
    def __init__(self, react_prompt: str):
        self.react_prompt = react_prompt.strip()

    def build(self, *, system_instruction: str) -> str:
        if not system_instruction.strip():
            raise ValueError("System instruction is required.")

        return f"""<system_instruction>
{system_instruction.strip()}
</system_instruction>

{self.react_prompt}
""".strip()


class AgentPromptContextBuilder:
    """Build system prompt context and user-message runtime metadata."""

    def __init__(
        self,
        *,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_files: bool = False,
        enable_agent_skills: bool = False,
        is_tool_offload_enabled: Callable[[], bool],
        skill_manager: Any = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents
        self.enable_workspace_files = enable_workspace_files or enable_subagents
        self.enable_agent_skills = enable_agent_skills
        self.is_tool_offload_enabled = is_tool_offload_enabled
        self.skill_manager = skill_manager
        self.clock = clock or datetime.now

    async def build_system_prompt(
        self,
        *,
        base_system_prompt: str,
        available_tools: set[str],
        tool_aliases: dict[str, str] | None = None,
        sub_agents: list[Any] | None = None,
    ) -> str:
        sections = [base_system_prompt]

        if self.enable_advanced_tool_use and "tools_retriever" in available_tools:
            sections.append(tools_retriever_additional_prompt)

        skills_context = ""
        if self.enable_agent_skills and self.skill_manager:
            skills_context = self.skill_manager.get_skills_context_xml()

        has_skill_tools = {
            "read_skill_file",
            "run_skill_script",
        }.issubset(available_tools)
        if skills_context and has_skill_tools:
            sections.append(agent_skills_additional_prompt)

        subagents_prompt = build_subagents_additional_prompt(
            enable_dynamic_spawn=(
                self.enable_subagents and "spawn_subagents" in available_tools
            ),
            enable_configured_subagents=bool(sub_agents),
        )
        if subagents_prompt:
            sections.append(subagents_prompt)

        has_workspace_tools = {
            "ls",
            "read_file",
            "write_file",
        }.issubset(available_tools)
        if self.enable_workspace_files and has_workspace_tools:
            sections.append(workspace_files_additional_prompt)

        if self.is_tool_offload_enabled() and "read_artifact" in available_tools:
            sections.append(artifact_tool_additional_prompt)

        if skills_context and has_skill_tools:
            sections.append(f"[AVAILABLE SKILLS]\n{skills_context}")

        # Only extension instructions use capability names. User instructions are
        # task content and must never be rewritten by tool alias mapping.
        aliases = tool_aliases or {}
        extensions = "\n".join(sections[1:])
        for name, exposed in aliases.items():
            if name != exposed:
                extensions = re.sub(
                    r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", exposed, extensions
                )
        return "\n".join([sections[0], extensions]).strip()

    def inject_current_datetime(self, messages: list[Message]) -> None:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role != "user":
                continue

            datetime_info = f"[CURRENT_DATETIME: {self.clock().strftime('%Y-%m-%d %H:%M:%S %Z')}]\n\n"
            messages[index] = Message(
                role="user",
                content=datetime_info + message.content,
            )
            return
