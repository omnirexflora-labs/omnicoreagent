import pytest

from omnicoreagent.core.agents.react_agent import ReactAgent
from omnicoreagent.runtime_config import AgentConfig


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


def test_react_agent_uses_base_run_loop(react_agent):
    assert callable(react_agent.run)
