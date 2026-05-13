from __future__ import annotations

import asyncio
import os
import threading

from fastapi.testclient import TestClient
import pytest

from omnicoreagent import OmniServe, OmniServeConfig
from omnicoreagent.background import BackgroundAgentManager
from omnicoreagent.core.events.event_router import EventRouter
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
        self.delay_seconds = 0.0
        self.fail_times = 0
        self.wait_until: threading.Event | None = None
        self.wait_timeout_seconds = 2.0

    async def connect_mcp_servers(self) -> None:
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned = True

    def generate_session_id(self) -> str:
        return "serve-background-session"

    async def run(self, query: str, session_id: str | None = None) -> dict:
        self.calls.append({"query": query, "session_id": session_id})
        if self.wait_until is not None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.wait_timeout_seconds
            while not self.wait_until.is_set() and loop.time() < deadline:
                await asyncio.sleep(0.01)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("planned failure")
        return {"response": f"completed:{session_id}:{query[-16:]}"}


class HeartbeatEventRouter(EventRouter):
    def __init__(self):
        super().__init__()
        self.heartbeat_seen = threading.Event()

    async def append(self, session_id, event):
        await super().append(session_id=session_id, event=event)
        if getattr(event.payload, "status", None) == "background_run_heartbeat":
            self.heartbeat_seen.set()


class SpyBackgroundAgentManager(BackgroundAgentManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_now_wait_values: list[bool] = []
        self.wait_for_run_called = False

    async def run_now(
        self,
        task_id: str,
        query: str | None = None,
        wait: bool = False,
        timeout_seconds: float | None = None,
    ):
        self.run_now_wait_values.append(wait)
        return await super().run_now(
            task_id,
            query=query,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )

    async def wait_for_run(
        self,
        run_id: str,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.05,
    ):
        self.wait_for_run_called = True
        return await super().wait_for_run(
            run_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )


def make_background_manager(tmp_path, *, lease_seconds=30, event_router=None):
    workspace = Workspace.from_config(
        {
            "workspace_backend": "local",
            "workspace_dir": str(tmp_path / "workspace"),
        }
    ).ensure()
    return BackgroundAgentManager(
        task_store="in_memory",
        event_router=event_router,
        workspace=workspace,
        lease_seconds=lease_seconds,
    )


def test_background_api_runs_task_and_exposes_events_and_workspace(tmp_path):
    agent = ServedAgent()
    event_router = HeartbeatEventRouter()
    agent.wait_until = event_router.heartbeat_seen
    manager = make_background_manager(
        tmp_path,
        lease_seconds=1,
        event_router=event_router,
    )
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
        replayed_events = events.json()["events"]
        event_names = [item["event"] for item in replayed_events]
        assert events.json()["count"] == len(replayed_events)
        assert [item["sequence"] for item in replayed_events] == list(
            range(1, len(replayed_events) + 1)
        )
        assert event_names[:3] == [
            "background_run_queued",
            "background_run_claimed",
            "background_run_started",
        ]
        assert "background_run_started" in event_names
        assert "background_run_heartbeat" in event_names
        assert "background_run_completed" in event_names
        claimed = next(
            item for item in replayed_events if item["event"] == "background_run_claimed"
        )
        assert claimed["worker_id"] == manager.worker_id
        assert claimed["lease_generation"] == 1
        assert claimed["lease_expires_at"]
        heartbeat = next(
            item for item in replayed_events if item["event"] == "background_run_heartbeat"
        )
        assert heartbeat["worker_id"] == manager.worker_id
        assert heartbeat["heartbeat_at"]
        assert client.get("/background/runs/missing/events").status_code == 404

        workspace = client.get(f"/background/runs/{run['run_id']}/workspace")
        assert workspace.status_code == 200
        workspace_files = {item["name"] for item in workspace.json()["files"]}
        assert {"run.json", "events.jsonl"}.issubset(workspace_files)

        deleted = client.delete("/background/tasks/daily_report", params={"delete_runs": True})
        assert deleted.status_code == 200
        assert client.delete("/background/tasks/daily_report").status_code == 404
        assert client.delete("/background/agents/served").status_code == 200
        assert client.delete("/background/agents/served").status_code == 404

    assert agent.connected is True
    assert agent.cleaned is True
    assert agent.calls
    assert agent.calls[0]["session_id"] == "background:served:daily_report"


def test_background_api_wait_true_uses_worker_wait_path(tmp_path):
    agent = ServedAgent()
    agent.delay_seconds = 0.05
    workspace = Workspace.from_config(
        {
            "workspace_backend": "local",
            "workspace_dir": str(tmp_path / "workspace"),
        }
    ).ensure()
    manager = SpyBackgroundAgentManager(task_store="in_memory", workspace=workspace)
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
        created = client.post(
            "/background/tasks",
            json={
                "task_id": "worker_wait",
                "query": "write with worker",
                "schedule": {"type": "manual"},
            },
        )
        assert created.status_code == 200

        run_response = client.post(
            "/background/tasks/worker_wait/run",
            json={"wait": True},
        )
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "completed"

    assert manager.run_now_wait_values == [False]
    assert manager.wait_for_run_called is True


def test_background_api_wait_true_executes_without_worker(tmp_path):
    agent = ServedAgent()
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="served",
            background_start_worker=False,
            request_timeout=2,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        created = client.post(
            "/background/tasks",
            json={
                "task_id": "manual_sync",
                "query": "write the synchronous result",
                "schedule": {"type": "manual"},
            },
        )
        assert created.status_code == 200

        run_response = client.post(
            "/background/tasks/manual_sync/run",
            json={"wait": True},
        )
        assert run_response.status_code == 200
        run = run_response.json()
        assert run["status"] == "completed"

        events = client.get(f"/background/runs/{run['run_id']}/events")
        assert events.status_code == 200
        assert [item["event"] for item in events.json()["events"]] == [
            "background_run_queued",
            "background_run_claimed",
            "background_run_started",
            "background_run_completed",
        ]

    assert len(agent.calls) == 1


def test_background_api_wait_true_without_worker_times_out_on_delayed_retry(tmp_path):
    agent = ServedAgent()
    agent.fail_times = 1
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="served",
            background_start_worker=False,
            request_timeout=1,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        created = client.post(
            "/background/tasks",
            json={
                "task_id": "retry_later",
                "query": "retry later",
                "schedule": {"type": "manual"},
                "retry_policy": {
                    "max_retries": 1,
                    "initial_delay_seconds": 5,
                    "max_delay_seconds": 5,
                },
            },
        )
        assert created.status_code == 200

        run_response = client.post(
            "/background/tasks/retry_later/run",
            json={"wait": True},
        )
        assert run_response.status_code == 504
        runs = client.get("/background/runs", params={"task_id": "retry_later"})
        assert runs.status_code == 200
        assert runs.json()["total"] == 1
        assert runs.json()["runs"][0]["status"] == "queued"

    assert len(agent.calls) == 1


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
