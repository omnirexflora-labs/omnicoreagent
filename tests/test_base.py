from unittest.mock import AsyncMock
import pytest
import json
from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.types import AgentState, Message, ParsedResponse, ToolCallResult, ToolError


@pytest.fixture
def agent():
    return BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
        request_limit=5,
        total_tokens_limit=1000,
    )


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_final_answer(agent):
    response = "<final_answer>It is sunny today.</final_answer>"
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.answer == "It is sunny today."


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_action(agent):
    response = '<tool_call><tool_name>search</tool_name><parameters>{"input": "news"}</parameters></tool_call>'
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.action is True
    assert result.tool_calls is True
    # data is a json string
    data = json.loads(result.data)
    assert data[0]["tool"] == "search"
    assert data[0]["parameters"]["input"] == "news"


@pytest.mark.broken_upstream
@pytest.mark.asyncio
async def test_extract_action_or_answer_fallback_error(agent):
    response = "This is just a general response without XML."
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.error is not None
    assert "Response must use XML format" in result.error


@pytest.mark.asyncio
async def test_update_llm_working_memory_empty(agent):
    message_history = AsyncMock(return_value=[])
    await agent.update_llm_working_memory(
        message_history=message_history,
        session_id="chat456",
        llm_connection=AsyncMock(),
        debug=False,
    )
    session_state = agent._get_session_state("chat456", debug=False)
    assert len(session_state.messages) == 0


@pytest.mark.asyncio
async def test_act_executes_tool_name_with_and_as_literal(agent):
    registry = ToolRegistry()
    history = []

    @registry.register_tool(
        name="search_and_read",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        description="Search and read in one call.",
    )
    async def search_and_read(query: str):
        return {"status": "success", "data": f"result for {query}"}

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data=json.dumps(
            [{"tool": "search_and_read", "parameters": {"query": "runtime"}}]
        ),
    )

    await agent.act(
        parsed_response=parsed_response,
        response="<tool_call><tool_name>search_and_read</tool_name></tool_call>",
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        sessions={},
        local_tools=registry,
        session_id="chat789",
    )

    tool_messages = [item for item in history if item["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["metadata"]["tool"] == "search_and_read"
    assert "result for runtime" in tool_messages[0]["content"]

    observations = [item for item in history if item["role"] == "user"]
    assert observations
    assert 'tool_name="search_and_read#1"' in observations[-1]["content"]


@pytest.mark.asyncio
async def test_act_records_one_tool_call_id_per_parallel_tool(agent):
    registry = ToolRegistry()
    history = []

    @registry.register_tool(
        name="alpha",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Alpha tool.",
    )
    async def alpha(value: str):
        return {"status": "success", "data": f"alpha:{value}"}

    @registry.register_tool(
        name="beta",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Beta tool.",
    )
    async def beta(value: str):
        return {"status": "success", "data": f"beta:{value}"}

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data=json.dumps(
            [
                {"tool": "alpha", "parameters": {"value": "one"}},
                {"tool": "beta", "parameters": {"value": "two"}},
            ]
        ),
    )

    await agent.act(
        parsed_response=parsed_response,
        response="<tool_calls>...</tool_calls>",
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        sessions={},
        local_tools=registry,
        session_id="chat790",
    )

    assistant_message = next(item for item in history if item["role"] == "assistant")
    tool_calls = assistant_message["metadata"]["tool_calls"]
    tool_messages = [item for item in history if item["role"] == "tool"]

    assert [call["function"]["name"] for call in tool_calls] == ["alpha", "beta"]
    assert [item["metadata"]["tool"] for item in tool_messages] == ["alpha", "beta"]
    assert {call["id"] for call in tool_calls} == {
        item["metadata"]["tool_call_id"] for item in tool_messages
    }


@pytest.mark.asyncio
async def test_run_prepares_internal_tools_once_for_prompt_and_execution(monkeypatch):
    agent = BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
        enable_advanced_tool_use=True,
    )
    build_count = 0
    history = []

    async def fake_build_internal_tools(registry):
        nonlocal build_count
        build_count += 1

        @registry.register_tool(
            name="internal_ping",
            inputSchema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            description="Internal ping tool.",
        )
        async def internal_ping(value: str):
            return {"status": "success", "data": f"pong:{value}"}

        return registry

    monkeypatch.setattr(
        "omnicoreagent.core.agents.base.build_tool_registry_advance_tools_use",
        fake_build_internal_tools,
    )

    class FakeLLMConnection:
        def __init__(self):
            self.calls = 0

        async def llm_call(self, messages):
            self.calls += 1
            if self.calls == 1:
                assert "internal_ping" in messages[0].content
                return """
<thought>Need internal tool.</thought>
<tool_call>
  <tool_name>internal_ping</tool_name>
  <parameters>
    <value>runtime</value>
  </parameters>
</tool_call>
"""
            return "<final_answer>done</final_answer>"

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(agent_name, session_id):
        return history

    result = await agent.run(
        system_prompt="system",
        query="run internal ping",
        llm_connection=FakeLLMConnection(),
        add_message_to_history=add_message_to_history,
        message_history=message_history,
        session_id="chat791",
    )

    tool_messages = [item for item in history if item["role"] == "tool"]
    assert result["answer"] == "done"
    assert build_count == 1
    assert len(tool_messages) == 1
    assert tool_messages[0]["metadata"]["tool"] == "internal_ping"
    assert "pong:runtime" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_handle_tool_validation_error_records_loop_and_event(agent):
    events = []
    session_state = agent._get_session_state(session_id="chat796", debug=False)

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    tool_batch_name, tool_batch_args, obs_text, results = (
        await agent._handle_tool_validation_error(
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
async def test_handle_tool_loop_state_marks_session_stuck(agent):
    events = []
    session_state = agent._get_session_state(session_id="chat800", debug=False)
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

    await agent._handle_tool_loop_state(
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
    )

    assert session_state.state == AgentState.STUCK
    assert session_state.loop_detector.reset_tool_name == "alpha"
    assert session_state.messages[0].role == "system"
    assert "Tool call loop detected" in session_state.messages[-1].content
    assert len(events) == 1
    assert events[0]["session_id"] == "chat800"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "alpha"
