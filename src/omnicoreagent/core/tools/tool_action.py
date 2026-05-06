from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from omnicoreagent.core.types import ParsedResponse, ToolError

TOOLS_RETRIEVER_TOOL_NAME = "tools_retriever"


@dataclass(frozen=True)
class ToolAction:
    """Validated model request for one tool call."""

    tool_name: str
    parameters: dict[str, Any]
    raw: dict[str, Any]

    @property
    def uses_tools_retriever(self) -> bool:
        return self.tool_name.lower() == TOOLS_RETRIEVER_TOOL_NAME


def parse_tool_actions(
    parsed_response: ParsedResponse,
) -> ToolError | list[ToolAction]:
    if not parsed_response.data:
        return ToolError(
            observation="Invalid tool call request: No data provided",
            tool_name="unknown",
            tool_args={},
        )

    try:
        payload = json.loads(parsed_response.data)
    except json.JSONDecodeError as exc:
        return ToolError(
            observation=f"Invalid tool call request: Invalid JSON: {exc}",
            tool_name="unknown",
            tool_args={},
        )

    raw_actions = payload if isinstance(payload, list) else [payload]
    if not raw_actions:
        return ToolError(
            observation="Invalid tool call request: No actions provided",
            tool_name="unknown",
            tool_args={},
        )

    actions: list[ToolAction] = []
    for index, raw_action in enumerate(raw_actions, start=1):
        action = _parse_single_action(raw_action=raw_action, index=index)
        if isinstance(action, ToolError):
            return action
        actions.append(action)

    return actions


def _parse_single_action(raw_action: Any, index: int) -> ToolError | ToolAction:
    if not isinstance(raw_action, dict):
        return ToolError(
            observation=(
                f"Invalid tool call request: Action #{index} must be an object."
            ),
            tool_name="unknown",
            tool_args={},
        )

    tool_name = raw_action.get("tool", "")
    if not isinstance(tool_name, str):
        return ToolError(
            observation="Invalid tool call request: Tool name must be a string.",
            tool_name="unknown",
            tool_args=raw_action.get("parameters", {}),
        )

    tool_name = tool_name.strip()
    parameters = raw_action.get("parameters", {})
    if parameters is None:
        parameters = {}

    if not tool_name:
        return ToolError(
            observation="No tool name provided in the request",
            tool_name="N/A",
            tool_args=parameters if isinstance(parameters, dict) else {},
        )

    if not isinstance(parameters, dict):
        return ToolError(
            observation=(
                f"Invalid tool call request: Parameters for '{tool_name}' must be an object."
            ),
            tool_name=tool_name,
            tool_args={},
        )

    return ToolAction(tool_name=tool_name, parameters=parameters, raw=raw_action)
