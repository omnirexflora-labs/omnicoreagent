from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.config import WorkspaceConfig


class HarnessHistory:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def add_message_to_history(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        self.items.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(
        self,
        session_id: str,
        agent_name: str | None = None,
    ) -> list[dict]:
        return [
            item
            for item in self.items
            if item["session_id"] == session_id
            and (agent_name is None or item["metadata"].get("agent_name") == agent_name)
        ]

    def called_tools(self) -> set[str]:
        return {
            item["metadata"].get("tool")
            for item in self.items
            if item["role"] == "tool"
        }


async def run_scripted_agent(
    *,
    tmp_path: Path,
    agent_name: str,
    llm,
    local_tools: ToolRegistry | None = None,
    workspace_files: bool = True,
    advanced_tools: bool = False,
    tool_offload: dict | None = None,
    max_steps: int = 10,
) -> tuple[dict, HarnessHistory, InMemoryTelemetryStore, Path]:
    workspace_dir = tmp_path / agent_name
    telemetry_store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(telemetry_store)
    history = HarnessHistory()

    agent = BaseReactAgent(
        agent_name=agent_name,
        max_steps=max_steps,
        tool_call_timeout=5,
        enable_advanced_tool_use=advanced_tools,
        enable_workspace_files=workspace_files,
        tool_offload_config=tool_offload or {"enabled": False},
        workspace_config=WorkspaceConfig(workspace_dir=workspace_dir),
    )
    context = await recorder.start_trace(
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
        session_id=f"session-{agent_name}",
        run_id=f"run-{agent_name}",
    )

    result = await agent.run(
        system_prompt=f"You are {agent_name}.",
        query="Run the real application task.",
        llm_connection=llm,
        add_message_to_history=history.add_message_to_history,
        message_history=history.message_history,
        sessions={},
        mcp_tools={},
        local_tools=local_tools,
        session_id=f"session-{agent_name}",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace(output={"answer": result["answer"]})
    assert context.trace_id
    return result, history, telemetry_store, workspace_dir


class DueDiligenceLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.artifact_id: str | None = None

    async def llm_call(self, messages):
        self.calls += 1
        transcript = "\n".join(
            getattr(message, "content", str(message)) for message in messages
        )
        if self.calls == 1:
            return """
<tool_calls>
  <tool_call>
    <tool_name>tools_retriever</tool_name>
    <parameters>{"query": "company market risk evidence workspace report artifact"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>company_profile</tool_name>
    <parameters>{"company": "OmniRetail AI"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>market_signals</tool_name>
    <parameters>{"company": "OmniRetail AI"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>risk_register</tool_name>
    <parameters>{"company": "OmniRetail AI"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>evidence_pack</tool_name>
    <parameters>{"company": "OmniRetail AI"}</parameters>
  </tool_call>
</tool_calls>
"""
        if self.calls == 2:
            match = re.search(r"read_artifact\('([^']+)'\)", transcript)
            assert match, transcript
            self.artifact_id = match.group(1)
            return f"""
<tool_calls>
  <tool_call>
    <tool_name>read_artifact</tool_name>
    <parameters>{{"artifact_id": "{self.artifact_id}"}}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>write_file</tool_name>
    <parameters>{{"path": "reports/omniretail-diligence.md", "content": "# OmniRetail AI\\n\\nInvestment view: proceed with focused risk review.", "mode": "create"}}</parameters>
  </tool_call>
</tool_calls>
"""
        return """
<final_answer>Due diligence complete with profile, market signals, risks, evidence artifact, and workspace memo.</final_answer>
"""


def build_due_diligence_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("company_profile")
    def company_profile(company: str) -> dict:
        return {"company": company, "stage": "Series B", "revenue": "$10M-$25M"}

    @tools.register_tool("market_signals")
    def market_signals(company: str) -> dict:
        return {
            "company": company,
            "tailwinds": ["agent adoption", "enterprise automation"],
        }

    @tools.register_tool("risk_register")
    def risk_register(company: str) -> dict:
        return {
            "company": company,
            "risks": [{"name": "sales cycle", "severity": "medium"}],
        }

    @tools.register_tool("evidence_pack")
    def evidence_pack(company: str) -> dict:
        return {
            "company": company,
            "evidence": "\n".join(
                f"{company} diligence evidence line {i}" for i in range(160)
            ),
        }

    return tools


@pytest.mark.asyncio
async def test_due_diligence_real_application_uses_parallel_tools_artifacts_workspace_and_telemetry(
    tmp_path,
):
    llm = DueDiligenceLlm()
    result, history, telemetry_store, workspace_dir = await run_scripted_agent(
        tmp_path=tmp_path,
        agent_name="due_diligence_real_app",
        llm=llm,
        local_tools=build_due_diligence_tools(),
        advanced_tools=True,
        tool_offload={
            "enabled": True,
            "threshold_tokens": 20,
            "threshold_bytes": 200,
            "max_preview_tokens": 20,
        },
        max_steps=8,
    )

    assert "Due diligence complete" in result["answer"]
    assert llm.artifact_id is not None
    assert history.called_tools() >= {
        "tools_retriever",
        "company_profile",
        "market_signals",
        "risk_register",
        "evidence_pack",
        "read_artifact",
        "write_file",
    }
    assert (workspace_dir / "files" / "reports" / "omniretail-diligence.md").exists()
    assert list((workspace_dir / "artifacts").glob("evidence_pack_*.txt"))

    traces = await telemetry_store.list_traces()
    assert len(traces) == 1
    event_types = {event.event_type for event in traces[0].events}
    assert {
        "tool_batch_start",
        "tool_result",
        "workspace_write",
        "workspace_offload",
    }.issubset(event_types)


class SupportOperationsLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def llm_call(self, messages):
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
        return """
<final_answer>Support plan ready: explain the delay, share timeline, and route the medium escalation.</final_answer>
"""


def build_support_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup_customer")
    def lookup_customer(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "plan": "enterprise",
            "name": "Ada Ventures",
        }

    @tools.register_tool("recent_orders")
    def recent_orders(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "orders": [{"id": "ord-1002", "status": "delayed"}],
        }

    @tools.register_tool("support_policy_search")
    def support_policy_search(query: str) -> dict:
        return {
            "query": query,
            "policy": "Escalate delayed enterprise shipments above $1000.",
        }

    @tools.register_tool("create_escalation")
    def create_escalation(ticket_id: str, severity: str, summary: str) -> dict:
        return {
            "ticket_id": ticket_id,
            "severity": severity,
            "summary": summary,
            "status": "queued",
        }

    return tools


@pytest.mark.asyncio
async def test_support_operations_real_application_tracks_ticket_state_in_workspace(
    tmp_path,
):
    result, history, telemetry_store, workspace_dir = await run_scripted_agent(
        tmp_path=tmp_path,
        agent_name="support_ops_real_app",
        llm=SupportOperationsLlm(),
        local_tools=build_support_tools(),
        max_steps=6,
    )

    assert "Support plan ready" in result["answer"]
    assert history.called_tools() >= {
        "lookup_customer",
        "recent_orders",
        "support_policy_search",
        "create_escalation",
        "write_file",
    }
    ticket = workspace_dir / "files" / "tickets" / "tck-1042.md"
    assert ticket.read_text(encoding="utf-8").startswith("# tck-1042")

    traces = await telemetry_store.list_traces()
    event_types = {event.event_type for event in traces[0].events}
    assert {"tool_batch_start", "tool_result", "workspace_write"}.issubset(event_types)


class WorkspaceCodeReviewLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def llm_call(self, messages):
        self.calls += 1
        if self.calls == 1:
            return """
<tool_calls>
  <tool_call>
    <tool_name>ls</tool_name>
    <parameters>{"path": ""}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>glob</tool_name>
    <parameters>{"pattern": "**/*.py"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>grep</tool_name>
    <parameters>{"pattern": "calculate_total", "include": "*.py"}</parameters>
  </tool_call>
</tool_calls>
"""
        if self.calls == 2:
            return """
<tool_call>
  <tool_name>read_file</tool_name>
  <parameters>{"path": "src/billing.py"}</parameters>
</tool_call>
"""
        if self.calls == 3:
            old = "def calculate_total(items):\\n    total = 0"
            new = "def calculate_total(items):\\n    if any(item['price'] < 0 for item in items):\\n        raise ValueError('negative prices are not allowed')\\n    total = 0"
            return f"""
<tool_calls>
  <tool_call>
    <tool_name>edit_file</tool_name>
    <parameters>{{"path": "src/billing.py", "old_str": "{old}", "new_str": "{new}"}}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>write_file</tool_name>
    <parameters>{{"path": "reviews/billing-review.md", "content": "# Billing review\\n\\nAdded negative price validation.", "mode": "create"}}</parameters>
  </tool_call>
</tool_calls>
"""
        return """
<final_answer>Workspace review complete. Billing validation was added and review notes were written.</final_answer>
"""


def seed_workspace(workspace_dir: Path) -> None:
    files_dir = workspace_dir / "files"
    (files_dir / "src").mkdir(parents=True)
    (files_dir / "tests").mkdir(parents=True)
    (files_dir / "src" / "billing.py").write_text(
        "def calculate_total(items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item['price']\n"
        "    return total\n",
        encoding="utf-8",
    )
    (files_dir / "tests" / "test_billing.py").write_text(
        "from src.billing import calculate_total\n\n"
        "def test_calculate_total():\n"
        "    assert calculate_total([{'price': 2}]) == 2\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_workspace_code_review_real_application_uses_builtin_file_commands(
    tmp_path,
):
    workspace_dir = tmp_path / "workspace_code_review_real_app"
    seed_workspace(workspace_dir)

    result, history, telemetry_store, _ = await run_scripted_agent(
        tmp_path=tmp_path,
        agent_name="workspace_code_review_real_app",
        llm=WorkspaceCodeReviewLlm(),
        local_tools=None,
        max_steps=8,
    )

    assert "Workspace review complete" in result["answer"]
    assert history.called_tools() >= {
        "ls",
        "glob",
        "grep",
        "read_file",
        "edit_file",
        "write_file",
    }
    billing_file = workspace_dir / "files" / "src" / "billing.py"
    assert "negative prices are not allowed" in billing_file.read_text(encoding="utf-8")
    review_file = workspace_dir / "files" / "reviews" / "billing-review.md"
    assert review_file.read_text(encoding="utf-8").startswith("# Billing review")

    traces = await telemetry_store.list_traces()
    event_types = {event.event_type for event in traces[0].events}
    assert {"workspace_read", "workspace_write"}.issubset(event_types)
