import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
import asyncio


class BaseToolHandler(ABC):
    @abstractmethod
    async def validate_tool_call_request(
        self,
        tool_data: dict[str, Any],
        available_tools: dict[str, Any] | list[str],
    ) -> Any:
        pass

    @abstractmethod
    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        pass


class MCPToolHandler(BaseToolHandler):
    def __init__(
        self,
        sessions: dict,
        server_name: str = None,
        tool_data: str = None,
        mcp_tools: dict = None,
    ):
        self.sessions = sessions
        self.server_name = server_name

        if self.server_name is None and tool_data and mcp_tools:
            self.server_name = self._infer_server_name(tool_data, mcp_tools)

    def _infer_server_name(
        self, tool_data: str, mcp_tools: dict[str, Any]
    ) -> str | None:
        try:
            action = json.loads(tool_data)
            input_tool_name = action.get("tool", "").strip().lower()

            for server_name, tools in mcp_tools.items():
                for tool in tools:
                    if tool.name.lower() == input_tool_name:
                        return server_name
        except (json.JSONDecodeError, AttributeError, KeyError):
            pass
        return None

    async def validate_tool_call_request(
        self, tool_data: str, mcp_tools: dict[str, Any]
    ) -> dict:
        try:
            action = json.loads(tool_data)
            input_tool_name = action.get("tool", "").strip()
            tool_args = action.get("parameters")

            if not input_tool_name:
                return {
                    "error": "Invalid JSON format. Check the action format again.",
                    "action": False,
                    "tool_name": input_tool_name,
                    "tool_args": tool_args,
                }

            input_tool_name_lower = input_tool_name.lower()

            for server_name, tools in mcp_tools.items():
                for tool in tools:
                    if tool.name.lower() == input_tool_name_lower:
                        return {
                            "action": True,
                            "tool_name": tool.name,
                            "tool_args": tool_args,
                            "server_name": server_name,
                        }

            return {
                "action": False,
                "error": f"The tool named '{input_tool_name}' does not exist in the available tools.",
                "tool_name": input_tool_name,
                "tool_args": tool_args,
            }

        except json.JSONDecodeError as e:
            return {
                "error": f"Json decode error: Invalid JSON format: {e}",
                "action": False,
                "tool_name": "N/A",
                "tool_args": None,
            }

    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        session = self.sessions[self.server_name]["session"]
        return await session.call_tool(tool_name, tool_args)


class LocalToolHandler(BaseToolHandler):
    def __init__(self, local_tools: Any = None):
        """Initialize LocalToolHandler with LocalToolsIntegration instance"""
        self.local_tools = local_tools

    async def validate_tool_call_request(
        self,
        tool_data: str,
        local_tools: Any = None,
    ) -> dict[str, Any]:
        try:
            action = json.loads(tool_data)
            tool_name = action.get("tool", "").strip()
            tool_args = action.get("parameters")

            if not tool_name or tool_args is None:
                return {
                    "error": "Missing 'tool' name or 'parameters' in the request.",
                    "action": False,
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                }

            available_local_tools = local_tools.get_available_tools()
            tool_names = [tool["name"] for tool in available_local_tools]

            if tool_name in tool_names:
                return {
                    "action": True,
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                }

            error_message = (
                f"The tool named '{tool_name}' does not exist in the current available tools. "
                "Please double-check the available tools before attempting another action.\n\n"
                "I will not retry the same tool name since it's not defined. "
                "If an alternative method or tool is available to fulfill the request, I'll try that now. "
                "Otherwise, I'll respond directly based on what I know."
            )
            return {
                "action": False,
                "error": error_message,
                "tool_name": tool_name,
                "tool_args": tool_args,
            }

        except json.JSONDecodeError:
            return {
                "error": "Invalid JSON format",
                "action": False,
                "tool_name": "N/A",
                "tool_args": None,
            }

    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """Execute a local tool using LocalToolsIntegration"""
        return await self.local_tools.execute_tool(tool_name, tool_args)


class ToolExecutor:
    def __init__(self, tool_handler: BaseToolHandler):
        self.tool_handler = tool_handler

    async def execute(
        self,
        agent_name: str,
        tool_name: str,
        tool_args: list[dict[str, Any]],
        tool_call_id: str,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str = None,
        **kwargs,
    ) -> str:
        """
        Executes one or more tools concurrently and always returns a single aggregated result object.
        Includes both successful and failed tool results under `tools_results`.
        Properly handles tool-level and global exceptions.
        """
        aggregated_results = []

        try:
            split_tool_names = tool_name.split("_and_")
            tasks = []

            for name, args in zip(split_tool_names, tool_args):
                tasks.append(self.tool_handler.call(name, args))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, args, result in zip(split_tool_names, tool_args, results):
                if isinstance(result, Exception):
                    aggregated_results.append(
                        {
                            "tool_name": name,
                            "args": args,
                            "status": "error",
                            "data": None,
                            "message": str(result),
                        }
                    )
                    continue

                if isinstance(result, dict):
                    status = result.get("status", "success")
                    data = result.get("data")
                    message = result.get("message")

                    if status == "error" and not message:
                        message = "Tool returned error status without message."

                    if status == "success" and data is None:
                        message = (
                            message
                            or "(Tool executed successfully but returned no data; This likely means the action completed or is async.)"
                        )

                elif hasattr(result, "content"):
                    content = result.content
                    data = content[0].text if isinstance(content, list) else content
                    status = "success"
                    message = None

                else:
                    data = result
                    status = "success" if result else "error"
                    message = (
                        None if result else f"Tool '{name}' returned empty output."
                    )

                aggregated_results.append(
                    {
                        "tool_name": name,
                        "args": args,
                        "status": status,
                        "data": data,
                        "message": message,
                    }
                )

                await add_message_to_history(
                    role="tool",
                    content=data if data is not None else message,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "tool": name,
                        "args": args,
                        "agent_name": agent_name,
                    },
                    session_id=session_id,
                )

            overall_status = (
                "error"
                if any(r["status"] == "error" for r in aggregated_results)
                else "success"
            )

            return json.dumps(
                {"status": overall_status, "tools_results": aggregated_results}
            )

        except Exception as e:
            aggregated_results.append(
                {
                    "tool_name": tool_name,
                    "args": tool_args,
                    "status": "error",
                    "data": None,
                    "message": str(e),
                }
            )

            await add_message_to_history(
                role="tool",
                content=str(e),
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "args": tool_args,
                    "agent_name": agent_name,
                },
                session_id=session_id,
            )

            return json.dumps({"status": "error", "tools_results": aggregated_results})
