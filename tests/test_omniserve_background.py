from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient
import pytest

from omnicoreagent import OmniServe, OmniServeConfig
from omnicoreagent.background import BackgroundAgentManager
from omnicoreagent.core.workspace.manager import Workspace
from omnicoreagent.serve.middleware.timeout import _route_manages_timeout


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

    async def connect_mcp_servers(self) -> None:
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned = True

    def generate_session_id(self) -> str:
        return "serve-background-session"

    async def run(
        self, query: str, session_id: str | None = None, run_id: str | None = None
    ) -> dict:
        self.calls.append({"query": query, "session_id": session_id, "run_id": run_id})
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("planned failure")
        return {"response": f"completed:{session_id}:{query[-16:]}"}


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


def make_background_manager(tmp_path, *, lease_seconds=30):
    workspace = Workspace.from_config(
        {
            "workspace_backend": "local",
            "workspace_dir": str(tmp_path / "workspace"),
        }
    ).ensure()
    return BackgroundAgentManager(
        task_store="in_memory",
        workspace=workspace,
        lease_seconds=lease_seconds,
    )


def test_background_api_runs_task_and_exposes_events_and_workspace(tmp_path):
    agent = ServedAgent()
    agent.delay_seconds = 0.35
    manager = make_background_manager(
        tmp_path,
        lease_seconds=1,
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
        status = client.get("/background/status")
        assert status.status_code == 200
        assert status.json()["agents"] == 1
        assert status.json()["tasks"] == 0
        assert status.json()["runs"] == 0
        assert status.json()["active_runs"] == 0

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

        task_status = client.get("/background/tasks/daily_report/status")
        assert task_status.status_code == 200
        assert task_status.json()["task_id"] == "daily_report"
        assert task_status.json()["enabled"] is False
        assert task_status.json()["runs"] == 0
        assert task_status.json()["schedule_state"]["task_id"] == "daily_report"

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

        task_status = client.get("/background/tasks/daily_report/status")
        assert task_status.status_code == 200
        assert task_status.json()["runs"] == 1
        assert task_status.json()["active_runs"] == 0
        assert task_status.json()["status_counts"]["completed"] == 1
        assert task_status.json()["latest_run"]["run_id"] == run["run_id"]

        status = client.get("/background/status")
        assert status.status_code == 200
        assert status.json()["tasks"] == 1
        assert status.json()["runs"] == 1
        assert status.json()["status_counts"]["completed"] == 1

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
        assert client.get("/background/tasks/missing/status").status_code == 404

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

    assert manager.run_now_wait_values == [True]
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
            request_timeout=2,
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
        timeout_detail = run_response.json()["detail"]
        assert timeout_detail["task_id"] == "retry_later"
        assert timeout_detail["status"] == "queued"
        assert timeout_detail["run_id"].startswith("run_")
        assert timeout_detail["wait_timeout_seconds"] == 1.5
        assert timeout_detail["request_timeout_seconds"] == 2
        runs = client.get("/background/runs", params={"task_id": "retry_later"})
        assert runs.status_code == 200
        assert runs.json()["total"] == 1
        assert runs.json()["runs"][0]["status"] == "queued"
        assert runs.json()["runs"][0]["run_id"] == timeout_detail["run_id"]

    assert len(agent.calls) == 1


def test_background_api_wait_true_without_request_timeout_returns_retry_state(tmp_path):
    agent = ServedAgent()
    agent.fail_times = 1
    manager = make_background_manager(tmp_path)
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="served",
            background_start_worker=False,
            request_timeout=0,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        created = client.post(
            "/background/tasks",
            json={
                "task_id": "retry_without_http_timeout",
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
            "/background/tasks/retry_without_http_timeout/run",
            json={"wait": True},
        )
        assert run_response.status_code == 200
        run = run_response.json()
        assert run["status"] == "queued"
        assert run["run_id"].startswith("run_")

    assert len(agent.calls) == 1


def test_background_api_wait_true_times_out_before_slow_inline_run_finishes(tmp_path):
    agent = ServedAgent()
    agent.delay_seconds = 2
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
                "task_id": "slow_inline",
                "query": "slow inline",
                "schedule": {"type": "manual"},
            },
        )
        assert created.status_code == 200

        run_response = client.post(
            "/background/tasks/slow_inline/run",
            json={"wait": True},
        )
        assert run_response.status_code == 504
        timeout_detail = run_response.json()["detail"]
        assert timeout_detail["task_id"] == "slow_inline"
        assert timeout_detail["run_id"].startswith("run_")
        assert timeout_detail["status"] in {"claimed", "running"}
        assert timeout_detail["wait_timeout_seconds"] == 0.5
        assert timeout_detail["request_timeout_seconds"] == 1

    assert len(agent.calls) == 1


def test_background_manual_run_route_owns_structured_timeout_with_api_prefix():
    assert _route_manages_timeout("/background/tasks/task-1/run") is True
    assert _route_manages_timeout("/api/v1/background/tasks/task-1/run") is True
    assert _route_manages_timeout("/background/tasks/task-1") is False
    assert _route_manages_timeout("/background/runs/run-1") is False


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

    assert response.status_code == 404

    schema = client.get("/openapi.json").json()
    assert not any(path.startswith("/background") for path in schema["paths"])


def test_background_openapi_matches_http_exception_response_shapes(tmp_path):
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
        schema = client.get("/openapi.json").json()
        delete_task_responses = schema["paths"]["/background/tasks/{task_id}"][
            "delete"
        ]["responses"]
        run_responses = schema["paths"]["/background/tasks/{task_id}/run"][
            "post"
        ]["responses"]
        task_status_schema = schema["components"]["schemas"][
            "BackgroundTaskStatusResponse"
        ]["properties"]

        assert (
            delete_task_responses["404"]["content"]["application/json"]["schema"][
                "$ref"
            ]
            == "#/components/schemas/HttpErrorResponse"
        )
        assert (
            run_responses["504"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/BackgroundRunTimeoutResponse"
        )
        assert task_status_schema["schedule"]["$ref"] == "#/components/schemas/ScheduleSpec"
        assert (
            task_status_schema["schedule_state"]["anyOf"][0]["$ref"]
            == "#/components/schemas/BackgroundScheduleState"
        )
        assert (
            task_status_schema["latest_run"]["anyOf"][0]["$ref"]
            == "#/components/schemas/BackgroundRun"
        )
