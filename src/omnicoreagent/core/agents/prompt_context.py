import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any

from omnicoreagent.core.system_prompts import (
    agent_skills_additional_prompt,
    artifact_tool_additional_prompt,
    dynamic_subagents_additional_prompt,
    memory_tool_additional_prompt,
    sub_agents_additional_prompt,
    tools_retriever_additional_prompt,
)
from omnicoreagent.core.types import Message
from omnicoreagent.core.utils import logger


class AgentPromptContextBuilder:
    """Build system prompt context and user-message runtime metadata."""

    def __init__(
        self,
        *,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_memory: bool = False,
        enable_agent_skills: bool = False,
        is_tool_offload_enabled: Callable[[], bool],
        skill_manager: Any = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents
        self.enable_workspace_memory = enable_workspace_memory
        self.enable_agent_skills = enable_agent_skills
        self.is_tool_offload_enabled = is_tool_offload_enabled
        self.skill_manager = skill_manager
        self.clock = clock or datetime.now

    async def build_system_prompt(
        self,
        *,
        base_system_prompt: str,
        tools_section: str,
        sub_agents: list[Any] | None = None,
    ) -> str:
        sections = [base_system_prompt]

        if self.enable_advanced_tool_use:
            sections.append(tools_retriever_additional_prompt)

        if self.enable_agent_skills and self.skill_manager:
            sections.append(agent_skills_additional_prompt)

        if self.enable_subagents:
            sections.append(dynamic_subagents_additional_prompt)

        if sub_agents:
            sections.append(sub_agents_additional_prompt)

        if self.enable_workspace_memory:
            sections.append(memory_tool_additional_prompt)

        if self.is_tool_offload_enabled():
            sections.append(artifact_tool_additional_prompt)

        if self.enable_agent_skills and self.skill_manager:
            skills_context = self.skill_manager.get_skills_context_xml()
            if skills_context:
                sections.append(f"[AVAILABLE SKILLS]\n{skills_context}")

        if sub_agents:
            sub_agents_registry = await self.render_sub_agents_registry(sub_agents)
            sections.append(f"[AVAILABLE SUB AGENTS REGISTRY]\n{sub_agents_registry}")

        sections.append(f"[AVAILABLE TOOLS REGISTRY]\n{tools_section}")
        return "\n".join(sections)

    def inject_current_datetime(self, messages: list[Message]) -> None:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role != "user":
                continue

            datetime_info = (
                f"[CURRENT_DATETIME: {self.clock().strftime('%Y-%m-%d %H:%M:%S %Z')}]\n\n"
            )
            messages[index] = Message(
                role="user",
                content=datetime_info + message.content,
            )
            return

    async def render_sub_agents_registry(self, sub_agents: list[Any]) -> str:
        if not sub_agents:
            return "No sub-agents available."

        registry = []

        for agent in sub_agents:
            try:
                signature = inspect.signature(agent.run)

                parameters = {}
                for param_name, param in signature.parameters.items():
                    if param_name == "self":
                        continue

                    is_required = param.default is inspect.Parameter.empty
                    param_type = "any"
                    if param.annotation != inspect.Parameter.empty:
                        param_type = (
                            param.annotation.__name__
                            if hasattr(param.annotation, "__name__")
                            else str(param.annotation)
                        )

                    parameters[param_name] = {
                        "type": param_type,
                        "required": is_required,
                        "default": None if is_required else param.default,
                    }

                registry.append(
                    {
                        "agent_name": agent.name,
                        "description": agent.system_instruction,
                        "parameters": parameters,
                    }
                )

            except Exception as e:
                logger.error(
                    f"Error processing agent {getattr(agent, 'name', 'unknown')}: {e}"
                )

        output_lines = [
            "════════════════════════════════════════════════════════════",
            "AVAILABLE SUB-AGENTS REGISTRY",
            "════════════════════════════════════════════════════════════",
            "",
        ]

        for index, agent_info in enumerate(registry, 1):
            output_lines.append(f"[{index}] {agent_info['agent_name']}")
            output_lines.append(f"    Description: {agent_info['description']}")

            if agent_info["parameters"]:
                output_lines.append("    Parameters:")
                for param_name, param_details in agent_info["parameters"].items():
                    required_label = (
                        "REQUIRED" if param_details["required"] else "optional"
                    )
                    default_label = (
                        f", default={param_details['default']}"
                        if not param_details["required"]
                        else ""
                    )
                    output_lines.append(
                        f"      • {param_name}: {param_details['type']} ({required_label}{default_label})"
                    )
            else:
                output_lines.append("    Parameters: None")

            output_lines.append("")

        return "\n".join(output_lines)
