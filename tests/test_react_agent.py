import pytest

from omnicoreagent.core.agents.react_agent import ReactAgent
from omnicoreagent.core.runtime.config import AgentConfig
from omnicoreagent.core.workspace_config import WorkspaceConfig


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


def test_agent_config_keeps_workspace_config_lazy_by_default(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    config = AgentConfig(agent_name="test_agent")

    assert config.workspace_config is None
    assert config.model_dump()["workspace_config"] is None


def test_react_agent_passes_workspace_config_to_tool_offloader(
    agent_config, monkeypatch, tmp_path
):
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "runtime-workspace"
    agent_config.tool_offload["enabled"] = True
    agent_config.tool_offload["threshold_tokens"] = 10
    agent_config.workspace_config = WorkspaceConfig(workspace_dir=workspace)

    agent = ReactAgent(config=agent_config)
    result = agent.tool_offloader.offload("runtime_tool", "Content " * 50)

    assert result.artifact_path.startswith(str((workspace / "artifacts").resolve()))
    assert (workspace / "artifacts").is_dir()
