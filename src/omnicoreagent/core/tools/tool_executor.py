from __future__ import annotations

from typing import Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler

RESULT_ENVELOPE_STATUSES = {"success", "partial", "error"}
RESULT_ENVELOPE_KEYS = {"status", "data", "message", "error"}


class ToolExecutor:
    """Execute one validated tool call and normalize its result."""

    def __init__(self, tool_handler: BaseToolHandler):
        self.tool_handler = tool_handler

    async def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
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

        return normalized

    def _normalize_result(
        self, tool_name: str, tool_args: dict[str, Any], result: Any
    ) -> dict[str, Any]:
        if isinstance(result, dict):
            is_result_envelope = self._is_result_envelope(result)
            if not is_result_envelope:
                status = "success"
                data = result
                message = None
            else:
                status = result.get("status", "success")
                data = result.get("data")
                message = result.get("message")

                if "error" in result and "status" not in result:
                    status = "error"
                    message = message or result.get("error")

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
            blocks = []
            for block in result.content or []:
                if hasattr(block, "model_dump"):
                    blocks.append(block.model_dump(exclude_none=True))
                elif isinstance(block, dict):
                    blocks.append(block)
                else:
                    blocks.append(
                        {"type": "text", "text": str(getattr(block, "text", block))}
                    )
            structured = getattr(result, "structuredContent", None)
            if (
                len(blocks) == 1
                and blocks[0].get("type") == "text"
                and structured is None
            ):
                data = blocks[0].get("text", "")
            else:
                data = {"content": blocks}
                if structured is not None:
                    data["structuredContent"] = structured
            status = "error" if getattr(result, "isError", False) else "success"
            message = (
                (
                    "\n".join(block.get("text", "") for block in blocks)
                    or "MCP tool failed"
                )
                if status == "error"
                else None
            )

        else:
            data = result
            status = "success"
            message = None

        return {
            "tool_name": tool_name,
            "args": tool_args,
            "status": status,
            "data": data,
            "message": message,
        }

    @staticmethod
    def _is_result_envelope(result: dict[str, Any]) -> bool:
        status = result.get("status")
        keys = set(result)

        if isinstance(status, str) and status in RESULT_ENVELOPE_STATUSES:
            return keys.issubset(RESULT_ENVELOPE_KEYS)

        if "status" in result:
            return False

        if "error" in result:
            return keys.issubset({"error", "message"})

        return False
