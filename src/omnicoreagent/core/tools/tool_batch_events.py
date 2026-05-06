from __future__ import annotations

import json
import uuid
from typing import Any

from omnicoreagent.core.events.base import (
    Event,
    EventType,
    ToolCallErrorPayload,
    ToolCallResultPayload,
    ToolCallStartedPayload,
)
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
) -> list[dict[str, Any]]:
    return [single_tool.tool_args for single_tool in tool_call_results]


def build_tool_call_history_metadata(
    agent_name: str,
    tool_call_results: list[ToolCallResult],
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
                    arguments=json.dumps(single_tool.tool_args),
                ),
            )
            for single_tool in tool_call_results
        ],
    )


def build_tool_call_started_event(
    agent_name: str,
    tool_batch_name: str,
    tool_batch_args: list[dict[str, Any]],
    first_tool_call_id: str,
) -> Event:
    return Event(
        type=EventType.TOOL_CALL_STARTED,
        payload=ToolCallStartedPayload(
            tool_name=tool_batch_name,
            tool_args=json.dumps(tool_batch_args),
            tool_call_id=first_tool_call_id,
        ),
        agent_name=agent_name,
    )


def build_tool_call_result_event(
    agent_name: str,
    tool_batch_name: str,
    tool_batch_args: list[dict[str, Any]],
    result: str,
    first_tool_call_id: str,
) -> Event:
    return Event(
        type=EventType.TOOL_CALL_RESULT,
        payload=ToolCallResultPayload(
            tool_name=tool_batch_name,
            tool_args=json.dumps(tool_batch_args),
            result=result,
            tool_call_id=first_tool_call_id,
        ),
        agent_name=agent_name,
    )


def build_tool_call_error_event(
    agent_name: str,
    tool_batch_name: str,
    error_message: str,
) -> Event:
    return Event(
        type=EventType.TOOL_CALL_ERROR,
        payload=ToolCallErrorPayload(
            tool_name=tool_batch_name,
            error_message=error_message,
        ),
        agent_name=agent_name,
    )
