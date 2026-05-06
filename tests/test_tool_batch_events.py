import json

from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.tools.tool_batch_events import (
    assign_tool_call_ids,
    build_tool_batch_args,
    build_tool_batch_name,
    build_tool_call_error_event,
    build_tool_call_history_metadata,
    build_tool_call_result_event,
    build_tool_call_started_event,
)
from omnicoreagent.core.types import ToolCallResult


def test_build_tool_batch_name_and_args_preserve_order():
    tool_calls = [
        ToolCallResult(tool_executor=None, tool_name="alpha", tool_args={"a": 1}),
        ToolCallResult(tool_executor=None, tool_name="beta", tool_args={"b": 2}),
    ]

    assert build_tool_batch_name(tool_calls) == "alpha, beta"
    assert build_tool_batch_args(tool_calls) == [{"a": 1}, {"b": 2}]


def test_build_tool_call_history_metadata_uses_assigned_ids():
    tool_calls = [
        ToolCallResult(tool_executor=None, tool_name="alpha", tool_args={"a": 1}),
        ToolCallResult(tool_executor=None, tool_name="beta", tool_args={"b": 2}),
    ]

    assign_tool_call_ids(tool_calls)
    metadata = build_tool_call_history_metadata(
        agent_name="runtime",
        tool_call_results=tool_calls,
    )
    dumped = metadata.model_dump()

    assert dumped["agent_name"] == "runtime"
    assert dumped["has_tool_calls"] is True
    assert dumped["tool_call_id"] == tool_calls[0].tool_call_id
    assert [call["id"] for call in dumped["tool_calls"]] == [
        tool_calls[0].tool_call_id,
        tool_calls[1].tool_call_id,
    ]
    assert [call["function"]["name"] for call in dumped["tool_calls"]] == [
        "alpha",
        "beta",
    ]
    assert json.loads(dumped["tool_calls"][0]["function"]["arguments"]) == {"a": 1}


def test_tool_batch_events_have_typed_payloads():
    started = build_tool_call_started_event(
        agent_name="runtime",
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"a": 1}, {"b": 2}],
        first_tool_call_id="tool-call-alpha",
    )
    result = build_tool_call_result_event(
        agent_name="runtime",
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"a": 1}, {"b": 2}],
        result="done",
        first_tool_call_id="tool-call-alpha",
    )
    error = build_tool_call_error_event(
        agent_name="runtime",
        tool_batch_name="alpha, beta",
        error_message="failed",
    )

    assert started.type == EventType.TOOL_CALL_STARTED
    assert started.payload.tool_call_id == "tool-call-alpha"
    assert json.loads(started.payload.tool_args) == [{"a": 1}, {"b": 2}]
    assert result.type == EventType.TOOL_CALL_RESULT
    assert result.payload.result == "done"
    assert error.type == EventType.TOOL_CALL_ERROR
    assert error.payload.error_message == "failed"
