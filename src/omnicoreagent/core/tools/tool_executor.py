from __future__ import annotations

from collections.abc import Callable
from typing import Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler


class ToolExecutor:
    """Execute one validated tool call and normalize its result."""

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
            is_result_envelope = any(
                key in result for key in ("status", "data", "message")
            )
            if not is_result_envelope:
                status = "success"
                data = result
                message = None
            else:
                status = result.get("status", "success")
                data = result.get("data")
                message = result.get("message")

                if status == "error" and not message:
                    message = (
                        result.get("error")
                        or "Tool returned error status without message."
                    )

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
