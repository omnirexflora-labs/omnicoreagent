from __future__ import annotations

from typing import Any

from omnicoreagent.core.logging import logger


class ToolPromptRenderer:
    """Render available local/MCP tools for the agent prompt."""

    def __init__(self, include_mcp_tools: bool = True):
        self.include_mcp_tools = include_mcp_tools

    async def render(
        self,
        mcp_tools: dict | None = None,
        local_tools: Any = None,
    ) -> str:
        lines = ["Available tools:"]

        try:
            if local_tools:
                self._append_local_tools(lines=lines, local_tools=local_tools)

            if mcp_tools and self.include_mcp_tools:
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
            self._append_schema_params(
                lines=lines,
                input_schema=tool.get("inputSchema", {}),
            )

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
                    self._append_schema_params(
                        lines=lines,
                        input_schema=tool.inputSchema,
                    )

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
                f"  - {param_name}: {param_type}{required_suffix} - {param_desc}"
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
