from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import CaptureState, TraceFilter
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_SECRET = "sk-header-secret-value"


class ScriptedModel:
    def __init__(self) -> None:
        self.calls = 0

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),),
                finish_reason="tool_calls",
            )
        return "done"


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 1}

    return tools


async def _agent(*, instruction="You are a header probe.", telemetry_config=None, **config):
    agent = OmniCoreAgent(
        name="header-agent",
        system_instruction=instruction,
        model_config={
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "api_key": _SECRET,
            "temperature": 0.2,
            "max_tokens": 512,
        },
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off", "max_steps": 7, **config},
        telemetry_config=telemetry_config,
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel()
    return agent


async def _run(agent, **kwargs):
    result = await agent.run("hello", session_id="header-session", **kwargs)
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    event = next(e for e in trace.events if e.event_type == "run_configuration")
    return trace, event


@pytest.mark.asyncio
async def test_run_header_records_the_harness_the_model_ran_with():
    agent = await _agent()
    trace, header = await _run(agent)
    config = header.metadata["run_configuration"]

    assert config["agent"]["name"] == "header-agent"
    assert config["model"] == {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "settings": {"temperature": 0.2, "max_tokens": 512},
    }
    assert config["limits"]["max_steps"] == 7
    assert config["limits"]["tool_call_timeout"] == 180
    assert config["context_management"]["strategy"] == "truncate"
    assert config["memory"]["mode"] == "sliding_window"
    assert config["tool_offload"]["enabled"] is True
    tools = config["tools"]
    assert "lookup" in tools["names"]
    assert tools["count"] == len(tools["names"])
    assert tools["names"] == sorted(tools["names"])
    assert tools["schema_digest"]
    assert config["system_prompt"]["digest"]
    assert config["system_prompt"]["bytes"] > 0
    assert config["fingerprints"]["privacy"]
    assert config["fingerprints"]["telemetry"]

    # The header is recorded before the first model step.
    types = [event.event_type for event in trace.events]
    assert types.index("run_configuration") < types.index("agent_step")


@pytest.mark.asyncio
async def test_run_header_never_records_model_credentials():
    agent = await _agent()
    trace, _ = await _run(agent)

    assert _SECRET not in json.dumps(trace.model_dump(), default=str)


@pytest.mark.asyncio
async def test_run_header_fills_trace_versions():
    agent = await _agent()
    trace, header = await _run(agent)
    metadata = trace.metadata

    assert metadata.prompt_version == header.metadata["run_configuration"]["system_prompt"]["digest"][:16]
    assert metadata.tool_schema_version == header.metadata["run_configuration"]["tools"]["schema_digest"][:16]
    assert metadata.memory_config_version
    assert metadata.agent_version
    assert metadata.agent_name == "header-agent"


@pytest.mark.asyncio
async def test_versions_are_stable_and_change_with_the_harness():
    first, _ = await _run(await _agent())
    second, _ = await _run(await _agent())
    changed, _ = await _run(await _agent(instruction="You are a different probe."))

    assert first.metadata.prompt_version == second.metadata.prompt_version
    assert first.metadata.tool_schema_version == second.metadata.tool_schema_version
    assert first.metadata.agent_version == second.metadata.agent_version
    assert changed.metadata.prompt_version != first.metadata.prompt_version
    assert changed.metadata.agent_version != first.metadata.agent_version


@pytest.mark.asyncio
async def test_explicit_agent_version_wins_over_the_content_hash():
    agent = await _agent(agent_version="support-bot-2.3.0")
    trace, header = await _run(agent)

    assert trace.metadata.agent_version == "support-bot-2.3.0"
    assert header.metadata["run_configuration"]["agent"]["version"] == "support-bot-2.3.0"


@pytest.mark.asyncio
async def test_system_prompt_text_follows_the_capture_policy():
    _, default_header = await _run(await _agent(telemetry_config={"capture": "default"}))
    _, full_header = await _run(await _agent())

    assert default_header.input is None
    assert default_header.input_capture.state == CaptureState.NOT_RECORDED
    assert "You are a header probe." in full_header.input["system_prompt"]


@pytest.mark.asyncio
async def test_run_accepts_tags_and_provenance():
    agent = await _agent()
    trace, _ = await _run(
        agent,
        tags=["nightly", "support"],
        provenance={
            "evaluation_id": "eval-7",
            "case_id": "case-42",
            "trial_id": "trial-3",
            "external_ids": {"ticket": "T-1001"},
        },
    )

    assert {"nightly", "support"} <= set(trace.metadata.tags)
    assert trace.provenance.evaluation_id == "eval-7"
    assert trace.provenance.case_id == "case-42"
    assert trace.provenance.trial_id == "trial-3"
    assert trace.provenance.external_ids == {"ticket": "T-1001"}


@pytest.mark.asyncio
async def test_direct_run_is_interactive():
    trace, _ = await _run(await _agent())

    assert trace.execution_surface == "interactive"


def test_served_run_records_the_serve_surface():
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = asyncio.run(_agent())
    client = TestClient(
        OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    )

    response = client.post(
        "/run/sync", json={"query": "hello", "session_id": "served-header"}
    )

    assert response.status_code == 200
    traces = asyncio.run(
        agent.telemetry_store.list_traces(TraceFilter(session_id="served-header"))
    )
    agent_trace = next(t for t in traces if t.spans[0].kind == "agent.run")
    assert agent_trace.execution_surface == "serve"


@pytest.mark.asyncio
async def test_background_run_records_the_background_surface():
    from omnicoreagent.background.manager import BackgroundAgentManager

    manager = BackgroundAgentManager(task_store="in_memory")
    agent = await _agent()
    await manager.register_agent("header-agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="header-agent",
        query="hello",
        schedule={"type": "manual"},
    )
    run = await manager.run_now("task", wait=True)

    background_trace_id = f"trace_background_{run.run_id}"
    [agent_trace] = [
        trace
        for trace in await manager.telemetry_store.list_traces()
        if trace.parent_trace_id == background_trace_id
    ]
    assert agent_trace.execution_surface == "background"


@pytest.mark.asyncio
async def test_run_header_is_recorded_when_outputs_are_not():
    agent = await _agent(telemetry_config={"record_outputs": False})
    trace, header = await _run(agent)

    assert header.metadata["run_configuration"]["limits"]["max_steps"] == 7
    assert trace.metadata.tool_schema_version


@pytest.mark.asyncio
async def test_run_header_links_to_the_first_model_context():
    trace, header = await _run(await _agent())
    config = header.metadata["run_configuration"]
    first_context = next(e for e in trace.events if e.event_type == "context_assembly")

    assert config["tools"]["schema_digest"] == first_context.output["tool_catalog_digest"]
    assert config["tools"]["names"] == first_context.output["tool_names"]
    assert (
        config["system_prompt"]["message_digests"][0]
        == first_context.output["message_digests"][0]
    )
