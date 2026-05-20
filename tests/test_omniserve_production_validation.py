from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from omnicoreagent import (
    BackgroundAgentManager,
    MemoryRouter,
    OmniCoreAgent,
    OmniServe,
    OmniServeConfig,
    ToolRegistry,
)
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.manager import Workspace
from omnicoreagent.serve.sse import stream_session_events


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

AUTH_HEADERS = {"Authorization": "Bearer validation-token"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "timeout", "retry_exhausted"}


@pytest.fixture(scope="module", autouse=True)
def load_local_validation_env():
    original_env = os.environ.copy()
    env_values: dict[str, str] = {}
    for path in (Path("src/.env"), Path(".env")):
        if path.exists():
            env_values.update(
                {
                    key: value
                    for key, value in dotenv_values(path).items()
                    if value is not None
                }
            )
    for key in (
        "REDIS_URL",
        "OMNICOREAGENT_TEST_REDIS_URL",
        "MONGODB_URI",
        "MONGODB_DB_NAME",
        "OMNICOREAGENT_TEST_MONGODB_URI",
        "OMNICOREAGENT_TEST_MONGODB_DATABASE",
        "AWS_S3_BUCKET",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL",
        "R2_BUCKET_NAME",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    ):
        value = env_values.get(key)
        if value:
            os.environ.setdefault(key, value)
    if os.getenv("REDIS_URL"):
        os.environ.setdefault("OMNICOREAGENT_TEST_REDIS_URL", os.environ["REDIS_URL"])
    if os.getenv("MONGODB_URI"):
        os.environ.setdefault("OMNICOREAGENT_TEST_MONGODB_URI", os.environ["MONGODB_URI"])
    if os.getenv("MONGODB_DB_NAME"):
        os.environ.setdefault(
            "OMNICOREAGENT_TEST_MONGODB_DATABASE",
            os.environ["MONGODB_DB_NAME"],
        )
    yield
    os.environ.clear()
    os.environ.update(original_env)


class ScriptedSupportOperationsLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def llm_call(self, messages: list[Any]):
        self.calls += 1
        if self.calls == 1:
            return """
<tool_calls>
  <tool_call>
    <tool_name>lookup_customer</tool_name>
    <parameters>{"customer_id": "cust-001"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>recent_orders</tool_name>
    <parameters>{"customer_id": "cust-001"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>support_policy_search</tool_name>
    <parameters>{"query": "enterprise delayed shipment escalation"}</parameters>
  </tool_call>
</tool_calls>
"""
        if self.calls == 2:
            conversation = _messages_text(messages)
            assert "Ada Ventures" in conversation
            assert "ord-1002" in conversation
            assert "Enterprise delayed shipments" in conversation
            return """
<tool_calls>
  <tool_call>
    <tool_name>create_escalation</tool_name>
    <parameters>{"ticket_id": "tck-1042", "severity": "medium", "summary": "Delayed enterprise shipment needs timeline and goodwill review."}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>write_file</tool_name>
    <parameters>{"path": "tickets/tck-1042.md", "content": "# tck-1042\\n\\nEscalated delayed shipment for Ada Ventures.", "mode": "create"}</parameters>
  </tool_call>
</tool_calls>
"""
        conversation = _messages_text(messages)
        assert "queued_for_specialist" in conversation
        assert "tickets/tck-1042.md" in conversation
        return """
<final_answer>Support plan ready: explain the delay, share timeline, and route the medium escalation.</final_answer>
"""


class DirectAnswerLlm:
    async def llm_call(self, messages: list[Any]):
        conversation = _messages_text(messages)
        assert "remember production validation" in conversation
        return "<final_answer>Production validation memory response stored.</final_answer>"


class WorkspaceBackgroundAgent:
    name = "workspace_background_validation_agent"
    system_instruction = "Write background validation output."
    model_config = {"provider": "openai", "model": "gpt-5.4-mini"}
    agent_config = {"enable_workspace_files": True}
    mcp_tools: list[dict[str, Any]] = []

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def connect_mcp_servers(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    def generate_session_id(self) -> str:
        return "workspace-background-validation"

    async def run(
        self,
        query: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_path = _workspace_path_from_query(query)
        self.workspace.files.write_text(
            f"{workspace_path}/output.md",
            f"# Workspace validation\n\nsession={session_id}\nrun={run_id}\n",
        )
        return {
            "response": f"workspace validation completed at {workspace_path}",
            "session_id": session_id,
            "run_id": run_id,
        }


def test_omniserve_real_app_sync_sse_telemetry_events_auth_rate_limit_and_prefix(
    tmp_path,
):
    from cookbook.omniserve.real_application_agent import create_agent

    workspace_dir = tmp_path / "served-support-app"
    agent = _attach_test_model_credentials(create_agent(workspace_dir=workspace_dir))
    config = OmniServeConfig(
        api_prefix="/api/v1",
        auth_enabled=True,
        auth_token="validation-token",
        rate_limit_enabled=True,
        rate_limit_requests=100,
        rate_limit_window=60,
        background_enabled=False,
        request_timeout=10,
    )
    server = OmniServe(agent, config)

    with TestClient(server.app) as client:
        assert client.get("/api/v1/health").status_code == 200
        ready = client.get("/api/v1/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True

        assert client.get("/api/v1/tools").status_code == 401
        tools = client.get("/api/v1/tools", headers=AUTH_HEADERS)
        assert tools.status_code == 200
        tool_names = {tool["name"] for tool in tools.json()["tools"]}
        assert {
            "lookup_customer",
            "recent_orders",
            "support_policy_search",
            "create_escalation",
            "write_file",
            "read_file",
            "grep",
        }.issubset(tool_names)

        agent.llm_connection = ScriptedSupportOperationsLlm()
        sync_response = client.post(
            "/api/v1/run/sync",
            json={
                "query": (
                    "Handle ticket tck-1042 for customer cust-001. Use the support "
                    "tools and save notes at tickets/tck-1042.md."
                ),
                "session_id": "production-validation-sync",
            },
            headers=AUTH_HEADERS,
        )
        assert sync_response.status_code == 200
        sync_payload = sync_response.json()
        assert sync_payload["response"].startswith("Support plan ready")
        assert sync_payload["trace_id"]
        assert sync_payload["run_id"]

        _assert_telemetry_http_endpoints(
            client,
            session_id="production-validation-sync",
            run_id=sync_payload["run_id"],
            trace_id=sync_payload["trace_id"],
            headers=AUTH_HEADERS,
        )
        _assert_http_sse_event_endpoints(
            agent,
            client,
            session_id="production-validation-sync",
            run_id=sync_payload["run_id"],
            headers=AUTH_HEADERS,
        )
        asyncio.run(
            _assert_event_stream_replay(
                agent,
                session_id="production-validation-sync",
                run_id=sync_payload["run_id"],
            )
        )

        agent.llm_connection = ScriptedSupportOperationsLlm()
        sse_payloads = _collect_sse(
            client,
            "POST",
            "/api/v1/run",
            headers=AUTH_HEADERS,
            json_body={
                "query": (
                    "Handle ticket tck-1042 for customer cust-001. Use the support "
                    "tools and save notes at tickets/tck-1042-stream.md."
                ),
                "session_id": "production-validation-sse",
            },
            stop_event="complete",
        )
        complete = next(item for item in sse_payloads if item["event"] == "complete")
        assert complete["data"]["response"].startswith("Support plan ready")
        assert complete["data"]["run_id"]
        assert complete["data"]["trace_id"]

        ticket = workspace_dir / "files" / "tickets" / "tck-1042.md"
        assert ticket.read_text(encoding="utf-8").startswith("# tck-1042")


def test_omniserve_rate_limit_protects_api_routes_and_exempts_readiness(tmp_path):
    from cookbook.omniserve.real_application_agent import create_agent

    agent = _attach_test_model_credentials(create_agent(workspace_dir=tmp_path))
    server = OmniServe(
        agent,
        OmniServeConfig(
            api_prefix="/api",
            auth_enabled=True,
            auth_token="validation-token",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window=60,
            background_enabled=False,
        ),
    )

    with TestClient(server.app) as client:
        health = client.get("/api/health")
        ready = client.get("/api/ready")
        assert health.status_code == 200
        assert ready.status_code == 200
        assert "X-RateLimit-Limit" not in health.headers
        assert "X-RateLimit-Limit" not in ready.headers

        allowed = client.get("/api/tools", headers=AUTH_HEADERS)
        denied = client.get("/api/tools", headers=AUTH_HEADERS)

        assert allowed.status_code == 200
        assert allowed.headers["X-RateLimit-Limit"] == "1"
        assert allowed.headers["X-RateLimit-Remaining"] == "0"
        assert denied.status_code == 429
        assert denied.headers["X-RateLimit-Limit"] == "1"


@pytest.mark.parametrize("memory_backend", ["in_memory", "sql", "redis", "mongodb"])
def test_omniserve_runtime_memory_backends_expose_session_history(
    memory_backend,
    tmp_path,
    monkeypatch,
):
    _configure_memory_backend_or_skip(memory_backend, tmp_path, monkeypatch)
    session_id = f"memory-validation-{memory_backend}-{uuid.uuid4().hex}"
    agent = _memory_validation_agent(memory_backend, tmp_path)
    server = OmniServe(agent, OmniServeConfig(background_enabled=False))

    try:
        with TestClient(server.app) as client:
            agent.llm_connection = DirectAnswerLlm()
            response = client.post(
                "/run/sync",
                json={
                    "query": "remember production validation for this session",
                    "session_id": session_id,
                },
            )
            assert response.status_code == 200
            assert response.json()["response"].startswith("Production validation")

            history = client.get(f"/sessions/{session_id}/history")
            assert history.status_code == 200
            messages = history.json()["messages"]
            assert len(messages) >= 2
            assert any(message["role"] == "user" for message in messages)
            assert any(message["role"] == "assistant" for message in messages)
    finally:
        asyncio.run(agent.memory_router.clear_memory(session_id, agent.name))
        asyncio.run(agent.cleanup())


@pytest.mark.parametrize("workspace_backend", ["local", "s3", "r2"])
def test_omniserve_background_workspace_backends_store_run_state(
    workspace_backend,
    tmp_path,
):
    workspace = _workspace_for_backend_or_skip(workspace_backend, tmp_path).ensure()
    manager = BackgroundAgentManager(
        task_store="in_memory",
        workspace=workspace,
        worker_id=f"validation_{workspace_backend}",
    )
    agent = WorkspaceBackgroundAgent(workspace)
    server = OmniServe(
        agent,
        OmniServeConfig(
            api_prefix="/api",
            auth_enabled=True,
            auth_token="validation-token",
            background_agent_id="workspace_agent",
            background_start_worker=True,
            request_timeout=30,
        ),
        background_manager=manager,
    )

    try:
        with TestClient(server.app) as client:
            assert client.get("/api/background/status").status_code == 401
            status = client.get("/api/background/status", headers=AUTH_HEADERS)
            assert status.status_code == 200
            assert status.json()["agents"] == 1

            task_id = f"workspace_validation_{workspace_backend}_{uuid.uuid4().hex}"
            created = client.post(
                "/api/background/tasks",
                json={
                    "task_id": task_id,
                    "query": "write durable workspace validation output",
                    "schedule": {"type": "manual"},
                    "retry_policy": {"max_retries": 0},
                },
                headers=AUTH_HEADERS,
            )
            assert created.status_code == 200

            run_response = client.post(
                f"/api/background/tasks/{task_id}/run",
                json={"wait": False},
                headers=AUTH_HEADERS,
            )
            assert run_response.status_code == 200
            queued_run = run_response.json()
            assert queued_run["status"] in {"queued", "claimed", "running", "completed"}
            run = _wait_for_background_run(
                client,
                queued_run["run_id"],
                headers=AUTH_HEADERS,
                timeout_seconds=30,
            )
            assert run["status"] == "completed"

            events_response = client.get(
                f"/api/background/runs/{run['run_id']}/events",
                headers=AUTH_HEADERS,
            )
            assert events_response.status_code == 200
            event_names = [event["event"] for event in events_response.json()["events"]]
            assert "background_run_completed" in event_names

            workspace_response = client.get(
                f"/api/background/runs/{run['run_id']}/workspace",
                headers=AUTH_HEADERS,
            )
            assert workspace_response.status_code == 200
            workspace_files = {item["name"] for item in workspace_response.json()["files"]}
            assert {"run.json", "events.jsonl", "output.md"}.issubset(workspace_files)

            if workspace_backend == "local":
                _assert_background_management_routes(client, task_id, run, AUTH_HEADERS)

        output = workspace.files.read_text(f"{run['workspace_path']}/output.md")
        assert "Workspace validation" in output
    finally:
        workspace.files.clear()
        workspace.artifacts.clear()


def _messages_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        else:
            parts.append(str(getattr(message, "content", message)))
    return "\n".join(parts)


def _attach_test_model_credentials(agent: OmniCoreAgent) -> OmniCoreAgent:
    agent.model_config = {**agent.model_config, "api_key": "test-key"}
    return agent


def _memory_validation_agent(memory_backend: str, tmp_path: Path) -> OmniCoreAgent:
    tools = ToolRegistry()

    @tools.register_tool("memory_marker")
    def memory_marker(value: str) -> dict[str, str]:
        return {"value": value}

    return OmniCoreAgent(
        name=f"memory_validation_{memory_backend}",
        system_instruction="Answer directly and keep session memory.",
        model_config={
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "api_key": "test-key",
        },
        local_tools=tools,
        memory_router=MemoryRouter(memory_backend),
        agent_config={
            "max_steps": 3,
            "enable_workspace_files": True,
            "workspace_config": {
                "workspace_backend": "local",
                "workspace_dir": str(tmp_path / f"workspace-{memory_backend}"),
            },
        },
    )


def _configure_memory_backend_or_skip(memory_backend: str, tmp_path: Path, monkeypatch):
    if memory_backend == "in_memory":
        return
    if memory_backend == "sql":
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory.db'}")
        from omnicoreagent.core.memory_store.sql_db_memory import get_sql_manager

        get_sql_manager().close_all()
        return
    if memory_backend == "redis":
        url = os.getenv("OMNICOREAGENT_TEST_REDIS_URL") or os.getenv("REDIS_URL")
        if not url:
            pytest.skip(
                "Redis memory validation requires OMNICOREAGENT_TEST_REDIS_URL or REDIS_URL"
            )
        _require_redis(url)
        monkeypatch.setenv("REDIS_URL", url)
        return
    if memory_backend == "mongodb":
        uri = os.getenv("OMNICOREAGENT_TEST_MONGODB_URI") or os.getenv("MONGODB_URI")
        if not uri:
            pytest.skip(
                "MongoDB memory validation requires OMNICOREAGENT_TEST_MONGODB_URI or MONGODB_URI"
            )
        _require_mongodb(uri)
        monkeypatch.setenv("MONGODB_URI", uri)
        monkeypatch.setenv(
            "MONGODB_COLLECTION",
            f"messages_validation_{uuid.uuid4().hex}",
        )
        return
    raise AssertionError(f"unknown memory backend: {memory_backend}")


def _workspace_for_backend_or_skip(workspace_backend: str, tmp_path: Path) -> Workspace:
    prefix = f"omnicoreagent-validation/{workspace_backend}/{uuid.uuid4().hex}"
    if workspace_backend == "local":
        return Workspace.from_config(
            WorkspaceConfig(
                workspace_backend="local",
                workspace_dir=tmp_path / "local-workspace",
                prefix=prefix,
            )
        )
    if workspace_backend == "s3":
        required = ["AWS_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
        _skip_if_missing_env(required, "S3 workspace validation")
        return Workspace.from_config(
            WorkspaceConfig(
                workspace_backend="s3",
                prefix=prefix,
                s3_bucket=os.environ["AWS_S3_BUCKET"],
                aws_region=os.getenv("AWS_REGION"),
                aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                aws_endpoint_url=os.getenv("AWS_ENDPOINT_URL"),
            )
        )
    if workspace_backend == "r2":
        required = [
            "R2_BUCKET_NAME",
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        ]
        _skip_if_missing_env(required, "R2 workspace validation")
        return Workspace.from_config(
            WorkspaceConfig(
                workspace_backend="r2",
                prefix=prefix,
                r2_bucket_name=os.environ["R2_BUCKET_NAME"],
                r2_account_id=os.environ["R2_ACCOUNT_ID"],
                r2_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                r2_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            )
        )
    raise AssertionError(f"unknown workspace backend: {workspace_backend}")


def _assert_telemetry_http_endpoints(
    client: TestClient,
    *,
    session_id: str,
    run_id: str,
    trace_id: str,
    headers: dict[str, str],
) -> None:
    events = client.get(
        "/api/v1/events/{session_id}/list".format(session_id=session_id),
        params={"run_id": run_id},
        headers=headers,
    )
    assert events.status_code == 200
    event_names = [event["event_type"] for event in events.json()["events"]]
    assert "serve_request_start" in event_names
    assert "tool_batch_start" in event_names
    assert "observation_pipeline_end" in event_names
    assert "workspace_write" in event_names
    assert "final_answer" in event_names

    trace = client.get(
        f"/api/v1/events/{session_id}/trace",
        params={"run_id": run_id},
        headers=headers,
    )
    assert trace.status_code == 200
    assert trace.json()["summary"]["trace_id"] == trace_id

    filtered_events = client.get(
        "/api/v1/telemetry/events",
        params={"session_id": session_id, "run_id": run_id, "event_type": "tool_result"},
        headers=headers,
    )
    assert filtered_events.status_code == 200
    assert filtered_events.json()["count"] >= 4

    traces = client.get(
        "/api/v1/telemetry/traces",
        params={"session_id": session_id, "run_id": run_id},
        headers=headers,
    )
    assert traces.status_code == 200
    assert traces.json()["count"] >= 1

    exact_trace = client.get(f"/api/v1/telemetry/traces/{trace_id}", headers=headers)
    assert exact_trace.status_code == 200
    assert exact_trace.json()["summary"]["trace_id"] == trace_id

    run_trace = client.get(f"/api/v1/telemetry/runs/{run_id}/trace", headers=headers)
    assert run_trace.status_code == 200
    assert run_trace.json()["summary"]["trace_id"] == trace_id

    session_trace = client.get(
        f"/api/v1/telemetry/sessions/{session_id}/trace",
        headers=headers,
    )
    assert session_trace.status_code == 200
    assert session_trace.json()["summary"]["run_id"] == run_id


async def _assert_event_stream_replay(
    agent: OmniCoreAgent,
    *,
    session_id: str,
    run_id: str,
) -> None:
    event_names: set[str | None] = set()
    stream = stream_session_events(agent, session_id, run_id=run_id)
    try:
        while "final_answer" not in event_names:
            chunk = await asyncio.wait_for(anext(stream), timeout=2)
            payload = _parse_sse_chunk(chunk)
            if payload is not None:
                event_names.add(payload["event"])
    finally:
        await stream.aclose()
    assert "session" in event_names
    assert "final_answer" in event_names


def _assert_http_sse_event_endpoints(
    agent: OmniCoreAgent,
    client: TestClient,
    *,
    session_id: str,
    run_id: str,
    headers: dict[str, str],
) -> None:
    original_stream = getattr(agent, "stream_telemetry_after", None)

    async def finite_stream(*, cursor, session_id, run_id):
        if False:
            yield {"cursor": cursor, "session_id": session_id, "run_id": run_id}

    agent.stream_telemetry_after = finite_stream
    try:
        event_stream = client.get(
            f"/api/v1/events/{session_id}?run_id={run_id}",
            headers=headers,
        )
        telemetry_stream = client.get(
            f"/api/v1/telemetry/events/stream?session_id={session_id}&run_id={run_id}",
            headers=headers,
        )
    finally:
        if original_stream is None:
            delattr(agent, "stream_telemetry_after")
        else:
            agent.stream_telemetry_after = original_stream

    assert event_stream.status_code == 200
    assert telemetry_stream.status_code == 200
    assert "event: session" in event_stream.text
    assert "event: final_answer" in event_stream.text
    assert "event: session" in telemetry_stream.text
    assert "event: final_answer" in telemetry_stream.text


def _assert_background_management_routes(
    client: TestClient,
    task_id: str,
    run: dict[str, Any],
    headers: dict[str, str],
) -> None:
    registered = client.post(
        "/api/background/agents",
        json={"agent_id": "spec_agent", "spec": {"agent_id": "spec_agent"}},
        headers=headers,
    )
    assert registered.status_code == 200

    agents = client.get("/api/background/agents", headers=headers)
    assert agents.status_code == 200
    assert {"workspace_agent", "spec_agent"}.issubset(
        {agent["agent_id"] for agent in agents.json()["agents"]}
    )

    spec_agent = client.get("/api/background/agents/spec_agent", headers=headers)
    assert spec_agent.status_code == 200
    assert spec_agent.json()["agent_id"] == "spec_agent"

    deleted_agent = client.delete(
        "/api/background/agents/spec_agent",
        params={"force": True},
        headers=headers,
    )
    assert deleted_agent.status_code == 200
    assert deleted_agent.json()["status"] == "deleted"

    tasks = client.get("/api/background/tasks", headers=headers)
    assert tasks.status_code == 200
    assert task_id in {task["task_id"] for task in tasks.json()["tasks"]}

    task = client.get(f"/api/background/tasks/{task_id}", headers=headers)
    assert task.status_code == 200
    assert task.json()["task_id"] == task_id

    patched = client.patch(
        f"/api/background/tasks/{task_id}",
        json={"metadata": {"validated": True}},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["metadata"]["validated"] is True

    paused = client.post(f"/api/background/tasks/{task_id}/pause", headers=headers)
    resumed = client.post(f"/api/background/tasks/{task_id}/resume", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "resumed"

    status = client.get(f"/api/background/tasks/{task_id}/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["latest_run"]["run_id"] == run["run_id"]

    runs = client.get(
        "/api/background/runs",
        params={"task_id": task_id, "status": "completed"},
        headers=headers,
    )
    assert runs.status_code == 200
    assert run["run_id"] in {item["run_id"] for item in runs.json()["runs"]}

    attempts = client.get(
        f"/api/background/runs/{run['run_id']}/attempts",
        headers=headers,
    )
    assert attempts.status_code == 200
    assert attempts.json()[0]["status"] == "completed"

    cancel = client.post(
        f"/api/background/runs/{run['run_id']}/cancel",
        headers=headers,
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancel_requested"

    deleted_task = client.delete(
        f"/api/background/tasks/{task_id}",
        params={"delete_runs": True},
        headers=headers,
    )
    assert deleted_task.status_code == 200
    assert deleted_task.json()["status"] == "deleted"


def _collect_sse(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    stop_event: str | None,
    max_payloads: int = 80,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    current_event: str | None = None
    kwargs: dict[str, Any] = {"headers": headers}
    if json_body is not None:
        kwargs["json"] = json_body
    with client.stream(method, path, **kwargs) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ").strip()
                continue
            if not line.startswith("data: "):
                continue
            data = json.loads(line.removeprefix("data: "))
            payloads.append({"event": current_event, "data": data})
            if stop_event is not None and current_event == stop_event:
                break
            if stop_event is None and any(
                payload["event"] == "final_answer" for payload in payloads
            ):
                break
            if len(payloads) >= max_payloads:
                break
    return payloads


def _parse_sse_chunk(chunk: str) -> dict[str, Any] | None:
    event_name: str | None = None
    data: dict[str, Any] | None = None
    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))
    if data is None:
        return None
    return {"event": event_name, "data": data}


def _workspace_path_from_query(query: str) -> str:
    marker = "workspace_path: /workspace/"
    if marker not in query:
        raise ValueError("background query missing workspace_path guidance")
    return query.split(marker, 1)[1].splitlines()[0].strip("/")


def _skip_if_missing_env(names: list[str], label: str) -> None:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip(f"{label} requires env vars: {', '.join(missing)}")


def _require_redis(url: str) -> None:
    async def ping() -> None:
        import redis.asyncio as redis

        client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(ping())
    except Exception as exc:
        if os.getenv("OMNICOREAGENT_TEST_REDIS_URL") or os.getenv("REDIS_URL"):
            raise AssertionError("Redis unavailable for production validation") from exc
        pytest.skip("Redis unavailable for production validation")


def _require_mongodb(uri: str) -> None:
    async def ping() -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=2000,
            connectTimeoutMS=2000,
            socketTimeoutMS=2000,
        )
        try:
            await client.admin.command("ping")
        finally:
            client.close()

    try:
        asyncio.run(ping())
    except Exception as exc:
        if os.getenv("OMNICOREAGENT_TEST_MONGODB_URI") or os.getenv("MONGODB_URI"):
            raise AssertionError("MongoDB unavailable for production validation") from exc
        pytest.skip("MongoDB unavailable for production validation")


def _wait_for_background_run(
    client: TestClient,
    run_id: str,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/background/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        last_run = response.json()
        if last_run["status"] in TERMINAL_RUN_STATUSES:
            return last_run
        time.sleep(0.05)
    raise AssertionError(f"background run did not finish: {last_run}")
