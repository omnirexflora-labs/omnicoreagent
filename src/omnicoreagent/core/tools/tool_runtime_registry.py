from typing import Any

from omnicoreagent.core.skills.tools import build_skill_tools
from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader
from omnicoreagent.core.tools.advance_tools_use import (
    build_tool_registry_advance_tools_use,
)
from omnicoreagent.core.tools.artifact_tool import build_tool_registry_artifact_tool
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.memory_tool.memory_tool import (
    build_tool_registry_memory_tool,
)
from omnicoreagent.core.utils import logger


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
        lines = ["Available tools:"]

        try:
            if local_tools:
                self._append_local_tools(lines=lines, local_tools=local_tools)

            if mcp_tools and not self.enable_advanced_tool_use:
                self._append_mcp_tools(lines=lines, mcp_tools=mcp_tools)

            if len(lines) == 1:
                return "No tools available"
        except Exception as e:
            logger.error(f"Error building compact tool registry: {e}")
            return "No tools available"

        return "\n".join(lines)

    def _append_local_tools(self, lines: list[str], local_tools: Any) -> None:
        local_tools_list = local_tools.get_available_tools()
        if not local_tools_list:
            return

        for tool in local_tools_list:
            if not isinstance(tool, dict):
                continue

            name = tool.get("name", "unknown")
            desc = tool.get("description", "").replace("\n", " ").strip()
            lines.append(f"\n{name}: {desc}")
            self._append_schema_params(lines=lines, input_schema=tool.get("inputSchema", {}))

    def _append_mcp_tools(self, lines: list[str], mcp_tools: dict) -> None:
        for _server_name, tools in mcp_tools.items():
            if not tools:
                continue
            for tool in tools:
                if not hasattr(tool, "name"):
                    continue

                name = str(tool.name)
                desc = str(tool.description).replace("\n", " ").strip()
                lines.append(f"\n{name}: {desc}")
                if hasattr(tool, "inputSchema") and tool.inputSchema:
                    self._append_schema_params(lines=lines, input_schema=tool.inputSchema)

    def _append_schema_params(
        self, lines: list[str], input_schema: dict[str, Any]
    ) -> None:
        params = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        if not params:
            return

        for param_name, param_info in params.items():
            param_type = self.format_param_type(param_info)
            param_desc = self.format_param_description(param_info)
            required_suffix = " (required)" if param_name in required else ""
            lines.append(
                f"  - {param_name}: {param_type}{required_suffix} — {param_desc}"
            )

    @staticmethod
    def format_param_type(param_info: dict) -> str:
        p_type = param_info.get("type", "any")

        if p_type == "array":
            items = param_info.get("items", {})
            if items:
                item_type = items.get("type", "any")
                if item_type == "object":
                    props = items.get("properties", {})
                    if props:
                        fields = ", ".join(
                            [
                                f'"{key}": {value.get("type", "any")}'
                                for key, value in props.items()
                            ]
                        )
                        return f"array of objects ({{{fields}}})"
                    return "array of objects"
                return f"array of {item_type}s"
            return "array"

        if p_type == "object":
            props = param_info.get("properties", {})
            if props:
                fields = ", ".join(
                    [
                        f'"{key}": {value.get("type", "any")}'
                        for key, value in props.items()
                    ]
                )
                return f"object ({{{fields}}})"
            return "object"

        return p_type

    @staticmethod
    def format_param_description(param_info: dict) -> str:
        param_desc = param_info.get("description", "").replace("\n", " ").strip()
        param_type = param_info.get("type", "any")

        if param_type == "array":
            items = param_info.get("items", {})
            if items.get("type") == "object":
                props = items.get("properties", {})
                if props:
                    example_fields = []
                    for key, value in props.items():
                        value_type = value.get("type", "any")
                        if value_type == "string":
                            example_fields.append(f'"{key}": "..."')
                        elif value_type == "number":
                            example_fields.append(f'"{key}": 0')
                        elif value_type == "boolean":
                            example_fields.append(f'"{key}": true')
                        else:
                            example_fields.append(f'"{key}": ...')

                    example = "{" + ", ".join(example_fields) + "}"
                    if param_desc:
                        param_desc += f". Example: {example}"
                    else:
                        param_desc = f"Example: {example}"

        return param_desc if param_desc else "No description"
