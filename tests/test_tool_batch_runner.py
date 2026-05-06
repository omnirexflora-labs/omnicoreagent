import pytest

from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.tools.tool_batch_runner import (
    TOOL_CALL_TIMEOUT_MESSAGE,
    ToolBatchRunner,
)
from omnicoreagent.core.types import AgentState, SessionState, ToolCallResult
from omnicoreagent.core.utils import RobustLoopDetector


@pytest.fixture
def runner():
    return ToolBatchRunner(agent_name="test_agent", tool_call_timeout=10)


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
async def test_handle_execution_error_records_history_and_event(runner, session_state):
    history = []
    events = []
    tool_calls = [
        ToolCallResult(
            tool_executor=None,
            tool_name="alpha",
            tool_args={"value": "one"},
            tool_call_id="tool-call-alpha",
        ),
        ToolCallResult(
            tool_executor=None,
            tool_name="beta",
            tool_args={"value": "two"},
            tool_call_id="tool-call-beta",
        ),
    ]

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

    results = await runner.handle_execution_error(
        tool_call_results=tool_calls,
        error_message=TOOL_CALL_TIMEOUT_MESSAGE,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat795",
        event_router=event_router,
        tool_batch_name="alpha, beta",
    )

    assert [item["role"] for item in history] == ["tool", "tool"]
    assert [item["metadata"]["tool"] for item in history] == ["alpha", "beta"]
    assert {item["metadata"]["tool_call_id"] for item in history} == {
        "tool-call-alpha",
        "tool-call-beta",
    }
    assert results == [
        {
            "tool_name": "alpha",
            "args": {"value": "one"},
            "status": "error",
            "data": None,
            "message": TOOL_CALL_TIMEOUT_MESSAGE,
        },
        {
            "tool_name": "beta",
            "args": {"value": "two"},
            "status": "error",
            "data": None,
            "message": TOOL_CALL_TIMEOUT_MESSAGE,
        },
    ]
    assert len(events) == 1
    assert events[0]["session_id"] == "chat795"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "alpha, beta"


@pytest.mark.asyncio
async def test_start_records_assistant_and_started_event(runner, session_state):
    history = []
    events = []
    tool_calls = [
        ToolCallResult(tool_executor=None, tool_name="alpha", tool_args={"value": "1"}),
        ToolCallResult(tool_executor=None, tool_name="beta", tool_args={"value": "2"}),
    ]

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

    tool_batch_name, tool_batch_args = await runner.start(
        tool_call_results=tool_calls,
        response="<tool_calls>...</tool_calls>",
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat797",
        event_router=event_router,
    )

    assert tool_batch_name == "alpha, beta"
    assert tool_batch_args == [{"value": "1"}, {"value": "2"}]
    assert all(tool.tool_call_id for tool in tool_calls)
    assert len({tool.tool_call_id for tool in tool_calls}) == 2
    assert history[0]["role"] == "assistant"
    assert [call["function"]["name"] for call in history[0]["metadata"]["tool_calls"]] == [
        "alpha",
        "beta",
    ]
    assert session_state.messages[-1].role == "assistant"
    assert len(events) == 1
    assert events[0]["event"].type == EventType.TOOL_CALL_STARTED
    assert events[0]["event"].payload.tool_name == "alpha, beta"


@pytest.mark.asyncio
async def test_execute_returns_observation_and_result_event(runner, session_state):
    history = []
    events = []

    class FakeExecutor:
        async def execute(
            self,
            agent_name,
            tool_args,
            tool_name,
            tool_call_id,
            add_message_to_history,
            session_id,
        ):
            await add_message_to_history(
                role="tool",
                content=f"{tool_name}:{tool_args['value']}",
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "args": tool_args,
                    "agent_name": agent_name,
                },
                session_id=session_id,
            )
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "success",
                "data": f"{tool_name}:{tool_args['value']}",
                "message": None,
            }

    tool_calls = [
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="alpha",
            tool_args={"value": "one"},
            tool_call_id="tool-call-alpha",
        ),
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="beta",
            tool_args={"value": "two"},
            tool_call_id="tool-call-beta",
        ),
    ]

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

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return "\n\n".join(
            f"{result['tool_name']}#1: {result['data']}" for result in tools_results
        )

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat798",
        event_router=event_router,
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"value": "one"}, {"value": "two"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert obs_text == "alpha#1: alpha:one\n\nbeta#1: beta:two"
    assert [result["tool_name"] for result in tools_results] == ["alpha", "beta"]
    assert [item["metadata"]["tool_call_id"] for item in history] == [
        "tool-call-alpha",
        "tool-call-beta",
    ]
    assert len(events) == 1
    assert events[0]["event"].type == EventType.TOOL_CALL_RESULT
    assert events[0]["event"].payload.tool_name == "alpha, beta"
