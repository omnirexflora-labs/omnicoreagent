import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from omnicoreagent.core.guardrails import PromptInjectionGuard

logger = logging.getLogger(__name__)


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
        guardrail: PromptInjectionGuard | None = None,
    ):
        self.sessions = sessions
        self.server_name = server_name
        self.guardrail = guardrail

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
        result = await session.call_tool(tool_name, tool_args)
        return self._scrub_mcp_result(tool_name, result)

    def _scrub_mcp_result(self, tool_name: str, result: Any) -> Any:
        """Scrub MCP tool result through guardrails at the client boundary.

        Defense-in-depth: checks MCP responses before they reach the
        tool executor aggregation layer. Only blocks DANGEROUS/CRITICAL.
        """
        if not self.guardrail:
            return result

        text = None
        if hasattr(result, "content") and isinstance(result.content, list):
            texts = [
                getattr(item, "text", None)
                for item in result.content
                if hasattr(item, "text")
            ]
            text = " ".join(t for t in texts if t)
        elif isinstance(result, dict):
            text = str(result.get("data") or result.get("message") or "")
        elif isinstance(result, str):
            text = result

        if not text or not text.strip():
            return result

        check = self.guardrail.check(text)
        if check.threat_level.value in ("dangerous", "critical"):
            logger.warning(
                f"Guardrail blocked MCP response from '{tool_name}' on "
                f"server '{self.server_name}': {check.threat_level.value} "
                f"(score: {check.threat_score})"
            )
            return {
                "status": "error",
                "data": None,
                "message": f"[MCP response blocked by guardrail: {check.message}]",
            }

        return result


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
        tool_args: dict[str, Any],
        tool_call_id: str,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Execute one validated tool call and normalize the result.

        Multi-tool concurrency is owned by the agent loop. Keeping this executor
        single-call avoids synthetic combined tool names and lets each call use
        its own handler, server, and tool_call_id.
        """
        try:
            result = await self.tool_handler.call(tool_name, tool_args)
            normalized = self._normalize_result(tool_name, tool_args, result)

        except Exception as e:
            normalized = {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "error",
                "data": None,
                "message": str(e),
            }

        await add_message_to_history(
            role="tool",
            content=normalized["data"]
            if normalized["data"] is not None
            else normalized["message"],
            metadata={
                "tool_call_id": tool_call_id,
                "tool": tool_name,
                "args": tool_args,
                "agent_name": agent_name,
            },
            session_id=session_id,
        )

        return normalized

    def _normalize_result(
        self, tool_name: str, tool_args: dict[str, Any], result: Any
    ) -> dict[str, Any]:
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
            message = None if result else f"Tool '{tool_name}' returned empty output."

        return {
            "tool_name": tool_name,
            "args": tool_args,
            "status": status,
            "data": data,
            "message": message,
        }
