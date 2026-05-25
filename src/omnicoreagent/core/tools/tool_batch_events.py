from __future__ import annotations

import json
import uuid
from typing import Any

from omnicoreagent.core.types import (
    ToolCall,
    ToolCallMetadata,
    ToolCallResult,
    ToolFunction,
)


def assign_tool_call_ids(tool_call_results: list[ToolCallResult]) -> None:
    for single_tool in tool_call_results:
        single_tool.tool_call_id = str(uuid.uuid4())


def build_tool_batch_name(tool_call_results: list[ToolCallResult]) -> str:
    return ", ".join([single_tool.tool_name for single_tool in tool_call_results])


def build_tool_batch_args(
    tool_call_results: list[ToolCallResult],
    *,
    redact: bool = False,
) -> list[dict[str, Any]]:
    if redact:
        return [_redacted_tool_args(single_tool.tool_args) for single_tool in tool_call_results]
    return [single_tool.tool_args for single_tool in tool_call_results]


def build_tool_call_history_metadata(
    agent_name: str,
    tool_call_results: list[ToolCallResult],
    *,
    redact_args: bool = False,
) -> ToolCallMetadata:
    return ToolCallMetadata(
        agent_name=agent_name,
        has_tool_calls=True,
        tool_call_id=tool_call_results[0].tool_call_id,
        tool_calls=[
            ToolCall(
                id=single_tool.tool_call_id,
                function=ToolFunction(
                    name=single_tool.tool_name,
                    arguments=json.dumps(
                        _redacted_tool_args(single_tool.tool_args)
                        if redact_args
                        else single_tool.tool_args
                    ),
                ),
            )
            for single_tool in tool_call_results
        ],
    )


def build_tool_call_history_content(
    response: str,
    tool_call_results: list[ToolCallResult],
    *,
    redact: bool = False,
) -> str:
    if not redact:
        return response

    calls = "\n".join(
        f"- {single_tool.tool_name}({', '.join(single_tool.tool_args)})"
        for single_tool in tool_call_results
    )
    return (
        "[GOVERNANCE] Assistant tool-call payload redacted before persistence.\n"
        "Tool names and argument keys:\n"
        f"{calls}"
    )


def _redacted_tool_args(tool_args: dict[str, Any]) -> dict[str, str]:
    if not tool_args:
        return {}
    return {key: "[REDACTED]" for key in tool_args}
