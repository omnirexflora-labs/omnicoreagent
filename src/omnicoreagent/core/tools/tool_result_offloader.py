from __future__ import annotations

import json
from typing import Any

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.types import ToolCallResult
from omnicoreagent.core.workspace.offload_policy import should_keep_tool_output_inline


class ToolResultOffloader:
    """Format normalized tool results and offload large tool output when enabled."""

    def __init__(self, tool_offloader: ToolResponseOffloader):
        self.tool_offloader = tool_offloader

    def maybe_offload_result(
        self,
        result: dict[str, Any],
        session_id: str | None,
        tool_call_result: ToolCallResult | None = None,
    ) -> dict[str, Any]:
        tool_name = result.get("tool_name", "unknown_tool")
        tool_provider = getattr(tool_call_result, "tool_provider", None) or result.get(
            "tool_provider"
        )
        data = result.get("data")

        if (
            data is None
            or not self.tool_offloader.config.enabled
            or should_keep_tool_output_inline(tool_provider)
        ):
            return result

        data_str = (
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, default=str)
        )
        if not self.tool_offloader.should_offload(data_str):
            return result

        offloaded = self.tool_offloader.offload(
            tool_name=tool_name,
            response=data_str,
            metadata={"args": result.get("args", {}), "session_id": session_id},
        )
        result["data"] = offloaded.context_message
        return result
