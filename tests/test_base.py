from unittest.mock import AsyncMock
import pytest
import json
from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.types import ParsedResponse


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
