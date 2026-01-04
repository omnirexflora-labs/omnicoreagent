from unittest.mock import AsyncMock

import pytest

from omnicoreagent.core.agents.react_agent import ReactAgent
from omnicoreagent.core.types import AgentConfig


@pytest.fixture
def agent_config():
    return AgentConfig(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=5,
        request_limit=100,
        total_tokens_limit=1000,
        mcp_enabled=True,
    )


@pytest.fixture
def react_agent(agent_config):
    return ReactAgent(config=agent_config)


def test_react_agent_initialization(agent_config):
    agent = ReactAgent(config=agent_config)

    assert agent.agent_name == "test_agent"
    assert agent.max_steps == 5
    assert agent.tool_call_timeout == 5
    assert agent.request_limit == 100
    assert agent.total_tokens_limit == 1000


@pytest.mark.asyncio
async def test_react_agent_run_executes_run(react_agent, monkeypatch):
    mock_response = {"result": "final answer"}
    mock_run = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(react_agent, "run", mock_run)

    result = await react_agent._run(
        system_prompt="You are an agent.",
        query="What's the weather?",
        llm_connection=AsyncMock(),
        add_message_to_history=AsyncMock(),
        message_history=AsyncMock(return_value=[]),
        debug=True,
        sessions={"chat_id": "chat123"},
        available_tools=["search"],
        tools_registry={},
        is_generic_agent=False,
        chat_id="chat123",
        event_router=AsyncMock(),
    )

    mock_run.assert_awaited_once()
    assert result == mock_response


@pytest.mark.asyncio
async def test_react_agent_run_with_minimal_kwargs(react_agent, monkeypatch):
    monkeypatch.setattr(react_agent, "run", AsyncMock(return_value={"result": "ok"}))

    result = await react_agent._run(
        system_prompt="SysPrompt",
        query="Minimal test",
        llm_connection=AsyncMock(),
        add_message_to_history=AsyncMock(),
        message_history=AsyncMock(),
        event_router=AsyncMock(),
    )

    assert result["result"] == "ok"
