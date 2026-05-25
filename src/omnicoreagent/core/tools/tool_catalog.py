from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MCPToolMatch:
    server_name: str
    tool: Any

    @property
    def tool_name(self) -> str:
        return str(getattr(self.tool, "name"))


def find_mcp_tool(
    tool_name: str,
    mcp_tools: dict[str, list[Any]] | None,
) -> MCPToolMatch | None:
    if not mcp_tools:
        return None

    normalized_name = tool_name.strip().lower()
    if not normalized_name:
        return None

    matches: list[MCPToolMatch] = []
    for server_name, tools in mcp_tools.items():
        for tool in tools:
            candidate = getattr(tool, "name", "")
            if str(candidate).lower() == normalized_name:
                matches.append(MCPToolMatch(server_name=server_name, tool=tool))

    if not matches:
        return None
    if len(matches) > 1:
        servers = ", ".join(sorted(match.server_name for match in matches))
        raise ValueError(
            f"MCP tool name '{tool_name}' is available on multiple servers "
            f"({servers}). Rename one MCP server tool; server-qualified MCP tool "
            "calls are not supported yet."
        )
    return matches[0]


def find_local_tool_name(tool_name: str, local_tools: Any) -> str | None:
    if not local_tools:
        return None

    available_tools = local_tools.get_available_tools()
    normalized_name = tool_name.strip().lower()
    for tool in available_tools:
        candidate = str(tool.get("name", ""))
        if candidate.lower() == normalized_name:
            return candidate

    return None
