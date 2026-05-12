from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

from omnicoreagent import OmniServe, OmniServeConfig
from omnicoreagent.background import BackgroundAgentManager
from omnicoreagent.core.workspace.manager import Workspace


@pytest.fixture(autouse=True)
def isolate_omniserve_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("OMNICOREAGENT_SERVE_", "OMNICOREAGENT_BACKGROUND_")):
            monkeypatch.delenv(key, raising=False)


class ServedAgent:
    name = "ServeBackgroundAgent"
    system_instruction = "Execute background tasks."
    model_config = {"provider": "openai", "model": "gpt-5.4-mini"}
    agent_config = {}
    mcp_tools = []

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.connected = False
        self.cleaned = False

    async def connect_mcp_servers(self) -> None:
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned = True

    def generate_session_id(self) -> str:
        return "serve-background-session"

    async def run(self, query: str, session_id: str | None = None) -> dict:
        self.calls.append({"query": query, "session_id": session_id})
        return {"response": f"completed:{session_id}:{query[-16:]}"}


def make_background_manager(tmp_path):
    workspace = Workspace.from_config(
        {
            "workspace_backend": "local",
            "workspace_dir": str(tmp_path / "workspace"),
        }
    ).ensure()
    return BackgroundAgentManager(task_store="in_memory", workspace=workspace)


def test_background_api_runs_task_and_exposes_events_and_workspace(tmp_path):
    agent = ServedAgent()
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="served",
            background_start_worker=True,
            request_timeout=2,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        agents = client.get("/background/agents")
        assert agents.status_code == 200
        assert agents.json()["agents"][0]["agent_id"] == "served"

        created = client.post(
            "/background/tasks",
            json={
                "task_id": "daily_report",
                "query": "write the durable report",
                "schedule": {"type": "manual"},
                "enabled": False,
                "retry_policy": {"max_retries": 0},
            },
        )
        assert created.status_code == 200
        assert created.json()["agent_id"] == "served"
        assert created.json()["enabled"] is False

        task = client.get("/background/tasks/daily_report")
        assert task.status_code == 200
        assert task.json()["enabled"] is False

        patched = client.patch(
            "/background/tasks/daily_report",
            json={"enabled": True, "metadata": {"owner": "ops"}},
        )
        assert patched.status_code == 200
        assert patched.json()["enabled"] is True
        assert patched.json()["metadata"] == {"owner": "ops"}

        run_response = client.post(
            "/background/tasks/daily_report/run",
            json={"wait": True},
        )
        assert run_response.status_code == 200
        run = run_response.json()
        assert run["status"] == "completed"
        assert run["result_preview"].startswith("completed:background:served:daily_report")

        latest = client.get(f"/background/runs/{run['run_id']}")
        assert latest.status_code == 200
        assert latest.json()["status"] == "completed"

        runs = client.get("/background/runs", params={"task_id": "daily_report"})
        assert runs.status_code == 200
        assert runs.json()["total"] == 1
        assert client.get("/background/runs", params={"status": "unknown"}).status_code == 422

        attempts = client.get(f"/background/runs/{run['run_id']}/attempts")
        assert attempts.status_code == 200
        assert attempts.json()[0]["status"] == "completed"

        events = client.get(f"/background/runs/{run['run_id']}/events")
        assert events.status_code == 200
        event_names = [item["event"] for item in events.json()["events"]]
        assert "background_run_started" in event_names
        assert "background_run_completed" in event_names
        assert client.get("/background/runs/missing/events").status_code == 404

        workspace = client.get(f"/background/runs/{run['run_id']}/workspace")
        assert workspace.status_code == 200
        workspace_files = {item["name"] for item in workspace.json()["files"]}
        assert {"run.json", "events.jsonl"}.issubset(workspace_files)

        deleted = client.delete("/background/tasks/daily_report", params={"delete_runs": True})
        assert deleted.status_code == 200
        assert client.delete("/background/agents/served").status_code == 200

    assert agent.connected is True
    assert agent.cleaned is True
    assert agent.calls
    assert agent.calls[0]["session_id"] == "background:served:daily_report"


def test_background_api_rejects_invalid_schedule_payload(tmp_path):
    agent = ServedAgent()
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="served",
            background_start_worker=False,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/background/tasks",
            json={
                "task_id": "bad_schedule",
                "query": "bad",
                "schedule": {"type": "manual", "seconds": 1},
            },
        )

    assert response.status_code == 422


def test_background_api_respects_auth_and_api_prefix(tmp_path):
    agent = ServedAgent()
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            api_prefix="/api",
            auth_enabled=True,
            auth_token="secret",
            background_agent_id="served",
            background_start_worker=False,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/background/agents").status_code == 401

        authed = client.get(
            "/api/background/agents",
            headers={"Authorization": "Bearer secret"},
        )
        assert authed.status_code == 200
        assert authed.json()["total"] == 1


def test_background_api_can_be_disabled():
    server = OmniServe(
        ServedAgent(),
        OmniServeConfig(background_enabled=False),
    )
    client = TestClient(server.app)

    response = client.get("/background/agents")

    assert response.status_code == 503
    assert response.json()["detail"] == "Background execution is disabled"
