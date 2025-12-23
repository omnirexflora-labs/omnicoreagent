from unittest.mock import AsyncMock

import pytest

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
async def test_extract_action_json_valid(agent):
    response = 'Action: {"tool": "search", "input": "weather"}'
    result = await agent.extract_action_json(response)
    assert result["action"] is True
    assert "data" in result


@pytest.mark.asyncio
async def test_extract_action_json_missing_action(agent):
    response = "Do something without action"
    result = await agent.extract_action_json(response)
    assert result["action"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_extract_action_json_unbalanced(agent):
    response = 'Action: {"tool": "search", "input": "weather"'
    result = await agent.extract_action_json(response)
    assert result["action"] is False
    assert "Unbalanced" in result["error"]


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_final_answer(agent):
    response = "Final Answer: It is sunny today."
    result = await agent.extract_action_or_answer(response)
    assert result.answer == "It is sunny today."


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_action(agent):
    response = 'Action: {"tool": "search", "input": "news"}'
    result = await agent.extract_action_or_answer(response)
    assert result.action is True
    assert isinstance(result.data, str)


@pytest.mark.asyncio
async def test_extract_action_or_answer_fallback(agent):
    response = "This is just a general response."
    result = await agent.extract_action_or_answer(response)
    assert result.answer == "This is just a general response."


@pytest.mark.asyncio
async def test_tool_call_execution(agent):
    # Simulate tool call result injected in state
    agent.state.tool_response = "The weather is sunny."

    # Next assistant message uses that response
    next_msg = await agent.extract_action_or_answer(
        "Final Answer: The tool said it is sunny."
    )
    assert next_msg.answer == "The tool said it is sunny."


@pytest.mark.asyncio
async def test_update_llm_working_memory_empty(agent):
    message_history = AsyncMock(return_value=[])
    await agent.update_llm_working_memory(message_history, "chat456")
    assert "test_agent" not in agent.messages or len(agent.messages["test_agent"]) == 0
