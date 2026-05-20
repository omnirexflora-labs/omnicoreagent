from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

from omnicoreagent import BackgroundAgentManager, OmniServe, OmniServeConfig  # noqa: E402
from omnicoreagent.core.workspace.manager import Workspace  # noqa: E402
from omnicoreagent.serve.cli import _load_agent_from_file  # noqa: E402


LLM_ENV_KEYS = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_API_VERSION",
    "OLLAMA_API_BASE",
    "CENCORI_API_KEY",
)


@pytest.fixture(autouse=True)
def isolate_serving_env(monkeypatch):
    """Keep local environment values from changing explicit server config."""
    saved_env = {key: os.environ.get(key) for key in LLM_ENV_KEYS}
    for key in list(os.environ):
        if key.startswith(("OMNICOREAGENT_SERVE_", "OMNICOREAGENT_BACKGROUND_")):
            monkeypatch.delenv(key, raising=False)
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def attach_test_model_credentials(agent):
    """Make runtime initialization explicit without depending on process env."""
    agent.model_config = {**agent.model_config, "api_key": "test-key"}
    return agent


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


def _messages_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        else:
            parts.append(str(getattr(message, "content", message)))
    return "\n".join(parts)


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
        "ls",
        "read_file",
        "write_file",
        "grep",
    }.issubset(tool_names)


def test_omniserve_real_application_sync_run_writes_workspace_and_trace(tmp_path):
    from cookbook.omniserve.real_application_agent import create_agent

    workspace_dir = tmp_path / "served-support-app"
    agent = attach_test_model_credentials(create_agent(workspace_dir=workspace_dir))
    server = OmniServe(agent, OmniServeConfig(background_enabled=False))

    with TestClient(server.app) as client:
        agent.llm_connection = ScriptedSupportOperationsLlm()
        response = client.post(
            "/run/sync",
            json={
                "query": (
                    "Handle ticket tck-1042 for customer cust-001. Use the support "
                    "tools and save notes at tickets/tck-1042.md."
                ),
                "session_id": "served-support-session",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["response"].startswith("Support plan ready")
        assert payload["session_id"] == "served-support-session"
        assert payload["trace_id"]
        assert payload["run_id"]

        events_response = client.get(
            f"/events/served-support-session/list?run_id={payload['run_id']}"
        )
        assert events_response.status_code == 200
        events_payload = events_response.json()
        assert events_payload["count"] > 0
        event_names = [event["event_type"] for event in events_payload["events"]]
        assert event_names[0] == "serve_request_start"
        assert "serve_request_end" in event_names
        assert "tool_batch_start" in event_names
        assert "observation_pipeline_end" in event_names
        assert "workspace_write" in event_names
        assert "final_answer" in event_names

        tool_results = [
            event["output"]
            for event in events_payload["events"]
            if event["event_type"] == "tool_result"
        ]
        tool_names = {result["tool_name"] for result in tool_results}
        assert {
            "lookup_customer",
            "recent_orders",
            "support_policy_search",
            "create_escalation",
        }.issubset(tool_names)
        assert any(result.get("data", {}).get("name") == "Ada Ventures" for result in tool_results)
        assert any("ord-1002" in str(result.get("data")) for result in tool_results)
        assert any(
            result.get("data", {}).get("status") == "queued_for_specialist"
            for result in tool_results
            if result["tool_name"] == "create_escalation"
        )

        trace_response = client.get(
            f"/events/served-support-session/trace?run_id={payload['run_id']}"
        )
        assert trace_response.status_code == 200
        trace_payload = trace_response.json()
        assert trace_payload["summary"]["trace_id"] == payload["trace_id"]
        assert trace_payload["summary"]["run_id"] == payload["run_id"]
        assert trace_payload["summary"]["status"] == "completed"
        assert trace_payload["summary"]["event_count"] >= 20
        assert [step["event_type"] for step in trace_payload["steps"]][-1] == "final_answer"

        telemetry_events_response = client.get(
            "/telemetry/events",
            params={
                "session_id": "served-support-session",
                "run_id": payload["run_id"],
                "event_type": "tool_result",
            },
        )
        assert telemetry_events_response.status_code == 200
        telemetry_events = telemetry_events_response.json()
        assert telemetry_events["count"] >= 4
        assert {
            event["output"]["tool_name"]
            for event in telemetry_events["events"]
        }.issuperset(
            {
                "lookup_customer",
                "recent_orders",
                "support_policy_search",
                "create_escalation",
            }
        )

        exact_trace_response = client.get(f"/telemetry/traces/{payload['trace_id']}")
        assert exact_trace_response.status_code == 200
        exact_trace = exact_trace_response.json()
        assert exact_trace["summary"]["trace_id"] == payload["trace_id"]
        assert exact_trace["trace"]["run_id"] == payload["run_id"]

        run_trace_response = client.get(f"/telemetry/runs/{payload['run_id']}/trace")
        assert run_trace_response.status_code == 200
        assert run_trace_response.json()["summary"]["trace_id"] == payload["trace_id"]

        session_trace_response = client.get(
            "/telemetry/sessions/served-support-session/trace"
        )
        assert session_trace_response.status_code == 200
        assert session_trace_response.json()["summary"]["run_id"] == payload["run_id"]

    ticket = workspace_dir / "files" / "tickets" / "tck-1042.md"
    assert ticket.read_text(encoding="utf-8").startswith("# tck-1042")


def test_omniserve_background_api_runs_real_application_background_agent(tmp_path):
    from cookbook.background_agents.real_application_background_task import (
        SupportOperationsBackgroundAgent,
    )

    workspace = Workspace.from_config(
        {
            "workspace_backend": "local",
            "workspace_dir": str(tmp_path / "background-workspace"),
        }
    ).ensure()
    agent = SupportOperationsBackgroundAgent(workspace)
    manager = BackgroundAgentManager(
        task_store="in_memory",
        workspace=workspace,
        worker_id="served_real_app_worker",
    )
    server = OmniServe(
        agent,
        OmniServeConfig(
            background_agent_id="support_ops",
            background_start_worker=True,
            request_timeout=2,
        ),
        background_manager=manager,
    )

    with TestClient(server.app) as client:
        created = client.post(
            "/background/tasks",
            json={
                "task_id": "support_ticket_tck_1042",
                "query": (
                    "Handle ticket tck-1042 for customer cust-001. Create a durable "
                    "support note and escalation summary."
                ),
                "schedule": {"type": "manual"},
                "timeout_seconds": 10,
                "retry_policy": {"max_retries": 0},
            },
        )
        assert created.status_code == 200

        queued_response = client.post(
            "/background/tasks/support_ticket_tck_1042/run",
            json={"wait": False},
        )
        assert queued_response.status_code == 200
        queued_run = queued_response.json()
        assert queued_run["status"] in {"queued", "running", "completed"}

        run = _wait_for_background_run(
            client,
            queued_run["run_id"],
            timeout_seconds=3,
        )
        assert run["status"] == "completed"

        events_response = client.get(f"/background/runs/{run['run_id']}/events")
        assert events_response.status_code == 200
        event_names = [event["event"] for event in events_response.json()["events"]]
        assert "background_run_completed" in event_names

        workspace_response = client.get(f"/background/runs/{run['run_id']}/workspace")
        assert workspace_response.status_code == 200
        workspace_files = {item["name"] for item in workspace_response.json()["files"]}
        assert {"events.jsonl", "output.md", "run.json", "tickets"}.issubset(
            workspace_files
        )

    output = workspace.files.read_text(f"{run['workspace_path']}/output.md")
    assert "Support background task: tck-1042" in output
    assert "Ada Ventures" in output
    assert "ord-1002" in output
    assert "Enterprise delayed shipments" in output
    assert "queued_for_specialist" in output

    ticket = workspace.files.read_text(f"{run['workspace_path']}/tickets/tck-1042.md")
    assert ticket == output


def _wait_for_background_run(
    client: TestClient,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/background/runs/{run_id}")
        assert response.status_code == 200
        last_run = response.json()
        if last_run["status"] in {
            "completed",
            "failed",
            "cancelled",
            "timeout",
            "retry_exhausted",
        }:
            return last_run
        time.sleep(0.05)
    raise AssertionError(f"background run did not finish: {last_run}")
