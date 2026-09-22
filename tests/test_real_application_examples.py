from __future__ import annotations
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
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
        self, session_id: str, agent_name: str | None = None
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
    return (result, history, telemetry_store, workspace_dir)


class DueDiligenceLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.artifact_id: str | None = None

    async def llm_call(self, messages, tools=None):
        self.calls += 1
        transcript = "\n".join(
            (getattr(message, "content", str(message)) for message in messages)
        )
        if self.calls == 1:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "tools_retriever",
                        '{"query": "company market risk evidence workspace report artifact"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_1",
                        "company_profile",
                        '{"company": "OmniRetail AI"}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_2",
                        "market_signals",
                        '{"company": "OmniRetail AI"}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_3",
                        "risk_register",
                        '{"company": "OmniRetail AI"}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_4",
                        "evidence_pack",
                        '{"company": "OmniRetail AI"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        if self.calls == 3:
            match = re.search("Artifact ID: (evidence_pack_[\\w]+)", transcript)
            assert match, transcript
            self.artifact_id = match.group(1)
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "read_artifact",
                        f'{{"artifact_id": "{self.artifact_id}"}}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_1",
                        "write_file",
                        '{"path": "reports/omniretail-diligence.md", "content": "# OmniRetail AI\\n\\nInvestment view: proceed with focused risk review.", "mode": "create"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        return "Due diligence complete with profile, market signals, risks, evidence artifact, and workspace memo."

    async def llm_stream(self, messages, tools=None):
        from omnicoreagent.core.agents.llm_response import normalize_model_turn

        turn = normalize_model_turn(await self.llm_call(messages, tools=tools))
        if turn.text:
            yield {"type": "text_delta", "text": turn.text}
        yield {"type": "turn_complete", "turn": turn}


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
                (f"{company} diligence evidence line {i}" for i in range(160))
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
    assert list((workspace_dir / "artifacts").glob("evidence_pack_*.json"))
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

    async def llm_call(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "lookup_customer",
                        '{"customer_id": "cust-001"}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_1",
                        "recent_orders",
                        '{"customer_id": "cust-001"}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_2",
                        "support_policy_search",
                        '{"query": "enterprise delayed shipment escalation"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "create_escalation",
                        '{"ticket_id": "tck-1042", "severity": "medium", "summary": "Delayed enterprise shipment needs timeline and goodwill review."}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_1",
                        "write_file",
                        '{"path": "tickets/tck-1042.md", "content": "# tck-1042\\n\\nEscalated delayed shipment for Ada Ventures.", "mode": "create"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        return "Support plan ready: explain the delay, share timeline, and route the medium escalation."

    async def llm_stream(self, messages, tools=None):
        from omnicoreagent.core.agents.llm_response import normalize_model_turn

        turn = normalize_model_turn(await self.llm_call(messages, tools=tools))
        if turn.text:
            yield {"type": "text_delta", "text": turn.text}
        yield {"type": "turn_complete", "turn": turn}


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

    async def llm_call(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(f"call_{self.calls}_0", "ls", '{"path": ""}'),
                    ToolRequest(
                        f"call_{self.calls}_1", "glob", '{"pattern": "**/*.py"}'
                    ),
                    ToolRequest(
                        f"call_{self.calls}_2",
                        "grep",
                        '{"pattern": "calculate_total", "include": "*.py"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "read_file",
                        '{"path": "src/billing.py"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        if self.calls == 3:
            old = "def calculate_total(items):\\n    total = 0"
            new = "def calculate_total(items):\\n    if any(item['price'] < 0 for item in items):\\n        raise ValueError('negative prices are not allowed')\\n    total = 0"
            return ModelTurn(
                tool_calls=(
                    ToolRequest(
                        f"call_{self.calls}_0",
                        "edit_file",
                        f'{{"path": "src/billing.py", "old_str": "{old}", "new_str": "{new}"}}',
                    ),
                    ToolRequest(
                        f"call_{self.calls}_1",
                        "write_file",
                        '{"path": "reviews/billing-review.md", "content": "# Billing review\\n\\nAdded negative price validation.", "mode": "create"}',
                    ),
                ),
                finish_reason="tool_calls",
            )
        return "Workspace review complete. Billing validation was added and review notes were written."

    async def llm_stream(self, messages, tools=None):
        from omnicoreagent.core.agents.llm_response import normalize_model_turn

        turn = normalize_model_turn(await self.llm_call(messages, tools=tools))
        if turn.text:
            yield {"type": "text_delta", "text": turn.text}
        yield {"type": "turn_complete", "turn": turn}


def seed_workspace(workspace_dir: Path) -> None:
    files_dir = workspace_dir / "files"
    (files_dir / "src").mkdir(parents=True)
    (files_dir / "tests").mkdir(parents=True)
    (files_dir / "src" / "billing.py").write_text(
        "def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item['price']\n    return total\n",
        encoding="utf-8",
    )
    (files_dir / "tests" / "test_billing.py").write_text(
        "from src.billing import calculate_total\n\ndef test_calculate_total():\n    assert calculate_total([{'price': 2}]) == 2\n",
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
