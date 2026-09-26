"""A policy refusing an operator's HTTP request is a 403, not a 500
(stranger test, round two, 2026-09-26).

The ops copilot's strict policy covered only its tools; creating a
background task over HTTP came back as `500 InternalServerError` with the
refusal in its detail.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig

POLICY = {
    "name": "tools-only",
    "mode": "strict",
    "rules": {"allow": [{"rule_id": "tools", "capability": "tool.local.call"}]},
}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = OmniCoreAgent(
        name="ops",
        system_instruction="x",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"},
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {"enabled": True, "policy": POLICY},
        },
    )
    asyncio.run(agent.initialize())
    return OmniServe(agent, OmniServeConfig(auth_enabled=False))


def test_a_background_task_the_policy_refuses_is_forbidden_and_says_why(server):
    with TestClient(server.app) as client:
        response = client.post(
            "/background/tasks",
            json={"task_id": "health", "query": "check health", "schedule": {"type": "manual"}},
        )

    assert response.status_code == 403, response.text
    assert "background.task.create" in response.json()["detail"]
