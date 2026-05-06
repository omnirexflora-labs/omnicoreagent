from typing import Any

from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_prompt_renderer import ToolPromptRenderer


async def build_tool_registry_advance_tools_use(registry: ToolRegistry):
    from omnicoreagent.core.tools.advance_tools_use import (
        build_tool_registry_advance_tools_use as build_advanced_tools,
    )

    return await build_advanced_tools(registry=registry)


def build_tool_registry_memory_tool(*, backend: Any, registry: ToolRegistry):
    from omnicoreagent.core.tools.memory_tool.memory_tool import (
        build_tool_registry_memory_tool as build_memory_tools,
    )

    return build_memory_tools(backend=backend, registry=registry)


def build_tool_registry_artifact_tool(
    *, offloader: ToolResponseOffloader, registry: ToolRegistry
):
    from omnicoreagent.core.tools.artifact_tool import (
        build_tool_registry_artifact_tool as build_artifact_tools,
    )

    return build_artifact_tools(offloader=offloader, registry=registry)


def build_skill_tools(*, skill_manager: Any, registry: ToolRegistry):
    from omnicoreagent.core.skills.tools import build_skill_tools as build_tools

    return build_tools(skill_manager=skill_manager, registry=registry)


class ToolRuntimeRegistry:
    """Prepare executable runtime tools and render tool schemas for prompts."""

    def __init__(
        self,
        register_internal_tool: ToolRegistry,
        tool_offloader: ToolResponseOffloader,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_memory: bool = False,
        enable_agent_skills: bool = False,
        skill_manager: Any = None,
    ):
        self.register_internal_tool = register_internal_tool
        self.tool_offloader = tool_offloader
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents
        self.enable_workspace_memory = enable_workspace_memory
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = skill_manager

    async def prepare_tools(self, local_tools: Any = None):
        registry = local_tools
        needs_internal_registry = (
            self.enable_advanced_tool_use
            or self.enable_subagents
            or self.enable_workspace_memory
            or self.tool_offloader.config.enabled
            or (self.enable_agent_skills and self.skill_manager)
        )

        if registry is None and needs_internal_registry:
            registry = self.register_internal_tool

        if registry is None:
            return None

        if self.enable_advanced_tool_use:
            await build_tool_registry_advance_tools_use(registry=registry)

        if self.enable_workspace_memory:
            build_tool_registry_memory_tool(
                backend=None,
                registry=registry,
            )

        if self.tool_offloader.config.enabled:
            build_tool_registry_artifact_tool(
                offloader=self.tool_offloader,
                registry=registry,
            )

        if self.enable_agent_skills and self.skill_manager:
            build_skill_tools(
                skill_manager=self.skill_manager,
                registry=registry,
            )

        return registry

    async def render_prompt_registry(
        self, mcp_tools: dict | None = None, local_tools: Any = None
    ) -> str:
        renderer = ToolPromptRenderer(
            include_mcp_tools=not self.enable_advanced_tool_use,
        )
        return await renderer.render(mcp_tools=mcp_tools, local_tools=local_tools)
