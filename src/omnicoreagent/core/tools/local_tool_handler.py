from __future__ import annotations

from typing import Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler


class LocalToolHandler(BaseToolHandler):
    """Execute tools from a local tool registry/integration."""

    def __init__(self, local_tools: Any):
        self.local_tools = local_tools

    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        return await self.local_tools.execute_tool(tool_name, tool_args)
