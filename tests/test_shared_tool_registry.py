"""Agents that share a ToolRegistry keep their own workspaces (Build
stranger test, 2026-09-27).

The runtime added its file tools, bound to the agent's workspace, into the
registry it was given. A lead and a child sharing one registry: the child's
run rebound them to the child's workspace, and the lead carried on with the
child's files ("File not found: plan.md" on a file the lead had listed).
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent, ToolRegistry
from test_run_suspend import RecordingModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


@pytest.mark.asyncio
async def test_a_lead_keeps_its_workspace_after_a_child_that_shares_its_tools(tmp_path):
    tools = ToolRegistry()

    @tools.register_tool("lookup")
    def lookup(topic: str) -> str:
        """Look a topic up."""
        return f"{topic}: noted"

    def agent(name, workspace, model, **kwargs):
        built = OmniCoreAgent(
            name=name, system_instruction="x", model_config=MODEL, local_tools=tools,
            agent_config={"guardrail_mode": "off", "workspace_config": {"workspace_dir": str(tmp_path / workspace)}},
            **kwargs,
        )
        return built, model

    child, child_model = agent("quizzer", "child-ws", RecordingModel([], "three questions"))
    lead, lead_model = agent(
        "lead", "lead-ws",
        RecordingModel(
            [("d1", "delegate_quizzer", '{"query": "quiz me"}'),
             ("w1", "write_file", '{"path": "plan.md", "content": "revise"}')],
            "plan written",
        ),
        sub_agents=[child],
    )
    await child.initialize()
    child.llm_connection = child_model
    await lead.initialize()
    lead.llm_connection = lead_model

    await lead.run("make a plan")

    assert (tmp_path / "lead-ws" / "files" / "plan.md").exists(), "written in the lead's workspace"
    assert not (tmp_path / "child-ws" / "files" / "plan.md").exists()
    assert set(tools.tools) == {"lookup"}, "the registry you passed is left as it was"
