from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

from omnicoreagent import OmniServe, OmniServeConfig  # noqa: E402
from omnicoreagent.serve.cli import _load_agent_from_file  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_serving_env(monkeypatch):
    """Keep local environment values from changing explicit server config."""
    for key in list(os.environ):
        if key.startswith(("OMNICOREAGENT_SERVE_", "OMNICOREAGENT_BACKGROUND_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)


def attach_test_model_credentials(agent):
    """Make runtime initialization explicit without depending on process env."""
    agent.model_config = {**agent.model_config, "api_key": "test-key"}
    return agent


def test_real_application_examples_are_importable_agent_factories(tmp_path):
    from cookbook.real_applications import (
        personal_operations_assistant,
        research_due_diligence_agent,
        support_operations_agent,
        workspace_code_review_agent,
    )

    personal = personal_operations_assistant.build_agent(tmp_path / "personal")
    research = research_due_diligence_agent.build_agent(tmp_path / "research")
    support = support_operations_agent.build_agent(tmp_path / "support")
    code_review = workspace_code_review_agent.build_agent(tmp_path / "code-review")

    assert personal.name == "personal_operations_assistant"
    assert research.name == "due_diligence_agent"
    assert support.name == "support_operations_agent"
    assert code_review.name == "workspace_code_review_agent"
    assert personal.agent_config["enable_workspace_files"] is True
    assert support.agent_config["enable_workspace_files"] is True


def test_personal_assistant_can_use_sql_memory_by_default_path(monkeypatch, tmp_path):
    from cookbook.real_applications import personal_operations_assistant

    monkeypatch.delenv("DATABASE_URL", raising=False)

    agent = personal_operations_assistant.build_agent(
        tmp_path / "personal-sql",
        memory_backend="sql",
    )

    assert agent.name == "personal_operations_assistant"
    assert agent.memory_router.get_memory_store_info()["type"] == "sql"
    assert (tmp_path / "personal-sql").is_dir()


def test_omniserve_loads_real_application_agent_file():
    agent = _load_agent_from_file("cookbook/omniserve/real_application_agent.py")

    assert agent.name == "support_operations_agent"
    assert agent.agent_config["enable_workspace_files"] is True
    assert agent.agent_config["workspace_config"]["workspace_backend"] == "local"


def test_omniserve_real_application_exposes_domain_and_workspace_tools(tmp_path):
    from cookbook.omniserve.real_application_agent import create_agent

    agent = attach_test_model_credentials(
        create_agent(workspace_dir=tmp_path / "served-support-app")
    )
    server = OmniServe(agent, OmniServeConfig(background_enabled=False))

    with TestClient(server.app) as client:
        tools_response = client.get("/tools")

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert {
        "lookup_customer",
        "recent_orders",
        "support_policy_search",
        "create_escalation",
    }.issubset(tool_names)
