import json

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.tools.tool_failure_handler import ToolFailureHandler
from omnicoreagent.core.types import (
    AgentState,
    Message,
    ParsedResponse,
    SessionState,
    ToolCallResult,
    ToolError,
)
from omnicoreagent.core.utils import RobustLoopDetector


@pytest.fixture
def handler():
    return ToolFailureHandler(agent_name="test_agent")


@pytest.fixture
def session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


@pytest.mark.asyncio
async def test_handle_validation_error_records_loop_and_event(handler, session_state):
    events = []

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    tool_batch_name, tool_batch_args, obs_text, results = (
        await handler.handle_validation_error(
            tool_error=ToolError(
                observation="The tool named 'missing_tool' does not exist.",
                tool_name="missing_tool",
                tool_args={"query": "runtime"},
            ),
            session_state=session_state,
            session_id="chat796",
            event_router=event_router,
        )
    )

    assert tool_batch_name == "missing_tool"
    assert tool_batch_args == [{"query": "runtime"}]
    assert obs_text == "The tool named 'missing_tool' does not exist."
    assert results == [
        {
            "tool_name": "missing_tool",
            "args": {"query": "runtime"},
            "status": "error",
            "data": None,
            "message": "The tool named 'missing_tool' does not exist.",
        }
    ]
    assert len(events) == 1
    assert events[0]["session_id"] == "chat796"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "missing_tool"


@pytest.mark.asyncio
async def test_handle_loop_state_marks_session_stuck(handler, session_state):
    events = []
    session_state.messages = [Message(role="system", content="system")]

    class FakeLoopDetector:
        def __init__(self):
            self.reset_tool_name = None

        def is_looping(self, tool_name):
            return tool_name == "alpha"

        def get_loop_type(self, tool_name):
            return ["consecutive_calls"]

        def reset(self, tool_name=None):
            self.reset_tool_name = tool_name

    session_state.loop_detector = FakeLoopDetector()

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    async def reset_system_prompt(messages, system_prompt):
        old_messages = messages[1:]
        return [Message(role="system", content=system_prompt), *old_messages]

    await handler.handle_loop_state(
        tool_call_results=[
            ToolCallResult(
                tool_executor=None,
                tool_name="alpha",
                tool_args={},
                tool_call_id="tool-call-alpha",
            )
        ],
        session_state=session_state,
        system_prompt="system",
        session_id="chat800",
        event_router=event_router,
        debug=False,
        reset_system_prompt=reset_system_prompt,
    )

    assert session_state.state == AgentState.STUCK
    assert session_state.loop_detector.reset_tool_name == "alpha"
    assert session_state.messages[0].role == "system"
    assert "Tool call loop detected" in session_state.messages[-1].content
    assert len(events) == 1
    assert events[0]["session_id"] == "chat800"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "alpha"


@pytest.mark.asyncio
async def test_handle_loop_state_ignores_malformed_tool_call_results(
    handler, session_state
):
    session_state.messages = [Message(role="system", content="system")]

    class FakeLoopDetector:
        def is_looping(self, tool_name):
            raise AssertionError("Malformed tool calls should be skipped")

    session_state.loop_detector = FakeLoopDetector()

    async def reset_system_prompt(messages, system_prompt):
        raise AssertionError("System prompt should not reset")

    await handler.handle_loop_state(
        tool_call_results=[object()],
        session_state=session_state,
        system_prompt="system",
        session_id="chat800",
        event_router=None,
        debug=False,
        reset_system_prompt=reset_system_prompt,
    )

    assert session_state.state == AgentState.IDLE
    assert session_state.messages == [Message(role="system", content="system")]


@pytest.mark.asyncio
async def test_act_routes_tool_validation_error_through_failure_handler():
    agent = BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
    )
    history = []
    events = []

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data=json.dumps([{"tool": "missing_tool", "parameters": {"query": "x"}}]),
    )

    await agent.act(
        parsed_response=parsed_response,
        response="<tool_call><tool_name>missing_tool</tool_name></tool_call>",
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        sessions={},
        local_tools=None,
        session_id="chat802",
        event_router=event_router,
    )

    observation_messages = [item for item in history if item["role"] == "user"]

    assert observation_messages
    assert "missing_tool" in observation_messages[-1]["content"]
    assert events
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "missing_tool"
