from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryExporter,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.config import WorkspaceConfig


class ScriptedHarnessLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.artifact_id: str | None = None

    async def llm_call(self, messages):
        self.calls += 1
        transcript = "\n".join(getattr(message, "content", str(message)) for message in messages)
        if self.calls == 1:
            return """
<tool_calls>
  <tool_call>
    <tool_name>tools_retriever</tool_name>
    <parameters>{"query": "find customer profile external risk workspace write and large report tools"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>customer_profile</tool_name>
    <parameters>{"customer_id": "cust-001"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>external_risk_lookup</tool_name>
    <parameters>{"customer_id": "cust-001"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>workspace_file_write</tool_name>
    <parameters>{"path": "notes/customer.md", "content": "customer cust-001 reviewed", "mode": "create"}</parameters>
  </tool_call>
  <tool_call>
    <tool_name>large_report</tool_name>
    <parameters>{"customer_id": "cust-001"}</parameters>
  </tool_call>
</tool_calls>
"""
        if self.calls == 2:
            match = re.search(r"read_artifact\('([^']+)'\)", transcript)
            assert match, transcript
            self.artifact_id = match.group(1)
            return f"""
<tool_call>
  <tool_name>read_artifact</tool_name>
  <parameters>{{"artifact_id": "{self.artifact_id}"}}</parameters>
</tool_call>
"""
        return """
<final_answer>Customer profile, external risk, workspace note, and full artifact were processed.</final_answer>
"""


class FakeMcpSession:
    async def call_tool(self, tool_name, tool_args):
        assert tool_name == "external_risk_lookup"
        return {
            "status": "success",
            "data": {
                "customer_id": tool_args["customer_id"],
                "risk": "low",
                "source": "fake-mcp",
            },
        }


@pytest.mark.asyncio
async def test_full_stack_harness_run_uses_tools_workspace_offload_and_telemetry(tmp_path):
    workspace_dir = tmp_path / "workspace"
    local_tools = ToolRegistry()

    @local_tools.register_tool("customer_profile")
    def customer_profile(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "tier": "enterprise",
            "region": "EMEA",
        }

    @local_tools.register_tool("large_report")
    def large_report(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "report": "\n".join(
                f"{customer_id} revenue evidence line {index}" for index in range(120)
            ),
        }

    agent = BaseReactAgent(
        agent_name="full_stack_smoke",
        max_steps=8,
        tool_call_timeout=5,
        enable_advanced_tool_use=True,
        enable_workspace_files=True,
        tool_offload_config={
            "enabled": True,
            "threshold_tokens": 20,
            "threshold_bytes": 200,
            "max_preview_tokens": 20,
        },
        workspace_config=WorkspaceConfig(workspace_dir=workspace_dir),
    )
    llm = ScriptedHarnessLlm()
    history = []
    telemetry_store = InMemoryTelemetryStore()
    exporter = InMemoryTelemetryExporter()
    recorder = TelemetryRecorder(telemetry_store, exporters=[exporter])
    context = await recorder.start_trace(
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="full_stack_smoke"),
        session_id="session-full-stack",
        run_id="run-full-stack",
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(session_id, agent_name=None):
        return [
            item
            for item in history
            if item["session_id"] == session_id
            and (agent_name is None or item["metadata"].get("agent_name") == agent_name)
        ]

    result = await agent.run(
        system_prompt="You are a production support agent.",
        query="Prepare the customer review.",
        llm_connection=llm,
        add_message_to_history=add_message_to_history,
        message_history=message_history,
        sessions={"crm": {"session": FakeMcpSession()}},
        mcp_tools={"crm": [SimpleNamespace(name="external_risk_lookup")]},
        local_tools=local_tools,
        session_id="session-full-stack",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace(output={"response": result["answer"]})

    assert "full artifact" in result["answer"]
    assert llm.artifact_id is not None
    assert (workspace_dir / "files" / "notes" / "customer.md").read_text() == (
        "customer cust-001 reviewed"
    )
    assert list((workspace_dir / "artifacts").glob("large_report_*.txt"))
    assert {item["metadata"].get("tool") for item in history if item["role"] == "tool"} >= {
        "tools_retriever",
        "customer_profile",
        "external_risk_lookup",
        "workspace_file_write",
        "large_report",
        "read_artifact",
    }

    trace = await telemetry_store.get_trace(context.trace_id)
    assert trace is not None
    event_types = {event.event_type for event in trace.events}
    assert {
        "tool_batch_start",
        "tool_call",
        "tool_result",
        "mcp_tool_call",
        "mcp_tool_result",
        "workspace_write",
        "workspace_read",
        "workspace_offload",
        "observation_pipeline_end",
    }.issubset(event_types)
    assert exporter.traces and exporter.traces[0].trace_id == context.trace_id
