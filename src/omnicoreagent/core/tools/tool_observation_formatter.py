from __future__ import annotations

from collections import defaultdict
from typing import Any

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.types import SessionState, ToolCallResult
from omnicoreagent.core.workspace.offload_policy import should_keep_tool_output_inline


class ToolObservationFormatter:
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

        data_str = data if isinstance(data, str) else str(data)
        if not self.tool_offloader.should_offload(data_str):
            return result

        offloaded = self.tool_offloader.offload(
            tool_name=tool_name,
            response=data_str,
            metadata={"args": result.get("args", {}), "session_id": session_id},
        )
        result["data"] = offloaded.context_message
        return result

    def build_results_observation(
        self,
        tool_call_results: list[ToolCallResult],
        tools_results: list[dict[str, Any]],
        session_state: SessionState,
        session_id: str | None,
    ) -> str:
        obs_lines = []
        success_count = 0
        error_count = 0
        tool_counter = defaultdict(int)
        seen_tools: set[str] = set()

        for index, result in enumerate(tools_results[: len(tool_call_results)]):
            result = self.maybe_offload_result(
                result=result,
                session_id=session_id,
                tool_call_result=tool_call_results[index],
            )
            tool_name = result.get("tool_name", "unknown_tool")
            args = result.get("args", {})
            status = result.get("status", "unknown")
            data = result.get("data")
            message = result.get("message", "")

            tool_counter[tool_name] += 1
            tool_call_generated_id = f"{tool_name}#{tool_counter[tool_name]}"
            display_value = data if data is not None else message
            if tool_name not in seen_tools:
                seen_tools.add(tool_name)
                session_state.loop_detector.record_tool_call(
                    str(tool_name),
                    str(args),
                    str(display_value),
                )

            if status == "success":
                obs_lines.append(f"{tool_call_generated_id}: {display_value}")
                success_count += 1
            elif status == "error":
                reason = display_value or "Unknown error occurred."
                obs_lines.append(f"{tool_call_generated_id} ERROR: {reason}")
                error_count += 1
            else:
                obs_lines.append(
                    f"{tool_call_generated_id}: Unexpected status '{status}'"
                )
                error_count += 1

        if success_count == len(tools_results):
            return "\n\n".join(obs_lines)
        if success_count > 0 and error_count > 0:
            return "Partial success:\n" + "\n\n".join(obs_lines)
        if error_count == len(tools_results):
            error_details = "\n\n".join(obs_lines)
            return f"Tool execution failed completely:\n{error_details}"
        return "\n\n".join(obs_lines) or "No valid tool results."
