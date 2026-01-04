from unittest.mock import AsyncMock
import pytest
import json
from omnicoreagent.core.agents.base import BaseReactAgent


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
