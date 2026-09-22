from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import JsonlTelemetryStore, TelemetryConfig
from omnicoreagent.core.telemetry.trajectory import (
    TRAJECTORY_VERSION,
    build_trajectory,
    trajectory_event_ids,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


class ScriptedModel:
    def __init__(self, *turns) -> None:
        self.turns = list(turns)

    async def llm_call(self, messages, tools=None, **kwargs):
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop")
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn),
            finish_reason="tool_calls",
        )


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 42}

    @tools.register_tool("explode", description="Always fails.")
    def explode() -> dict:
        raise RuntimeError("tool failed")

    return tools


async def _child(telemetry_config) -> OmniCoreAgent:
    child = OmniCoreAgent(
        name="researcher",
        system_instruction="You research.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
        telemetry_config=telemetry_config,
    )
    await child.initialize()
    child.llm_connection = ScriptedModel(
        [("child_call", "lookup", '{"key": "child"}')], "child done"
    )
    return child


async def _parent(telemetry_config=None):
    telemetry_config = telemetry_config or TelemetryConfig(capture="full")
    agent = OmniCoreAgent(
        name="lead",
        system_instruction="You lead.",
        model_config=_MODEL,
        local_tools=_tools(),
        sub_agents=[await _child(telemetry_config)],
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
        telemetry_config=telemetry_config,
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(
        [
            ("call_ok", "lookup", '{"key": "a"}'),
            ("call_err", "explode", "{}"),
            ("call_bad", "lookup", "{broken"),
        ],
        [("call_child", "delegate_researcher", '{"query": "dig"}')],
        "done",
    )
    return agent


async def _run(agent):
    result = await agent.run("go", session_id="trajectory")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    return result, trace, await agent.get_trajectory(result["trace_id"])


@pytest.mark.asyncio
async def test_trajectory_reads_the_run_from_request_to_final_answer():
    _, trace, trajectory = await _run(await _parent())

    assert trajectory["trajectory_version"] == TRAJECTORY_VERSION
    assert trajectory["request"]["message"] == "go"
    assert trajectory["harness"]["tools"]["count"] >= 3
    assert [m["kind"] for m in trajectory["runtime_messages"]] == ["current_datetime"]
    assert [step["step"] for step in trajectory["steps"]] == [1, 2, 3]

    first, second, third = trajectory["steps"]
    assert [(c["tool_call_id"], c["outcome"]) for c in first["tool_calls"]] == [
        ("call_ok", "success"),
        ("call_err", "error"),
        ("call_bad", "rejected"),
    ]
    assert first["tool_calls"][2]["raw_arguments"] == "{broken"
    assert first["tool_calls"][2]["rejection_reason"] == "invalid_arguments"
    assert [m["purpose"] for m in first["model_calls"]] == ["agent_turn"]
    assert first["model_calls"][0]["facts"]["finish_reason"] == "tool_calls"

    # The next model call received exactly step 1's observations.
    observation_ids = [c["observation"]["event_id"] for c in first["tool_calls"]]
    assert second["model_calls"][0]["new_observation_event_ids"] == observation_ids
    # ...and each observation is the exact content that call sent to the model.
    sent = {
        message["tool_call_id"]: message["content"]
        for message in second["model_calls"][0]["request"]["messages"]
        if message.get("role") == "tool"
    }
    for call in first["tool_calls"]:
        assert sent[call["tool_call_id"]] == call["observation"]["content"]

    final_call = third["model_calls"][0]
    assert trajectory["final"]["type"] == "final_answer"
    assert trajectory["final"]["output"]["response"] == "done"
    assert trajectory["final"]["final_model_response_event_id"] == final_call["response_event_id"]
    assert trajectory["totals"]["tool_calls"]["total"] == 4
    assert trajectory["status"] == "completed"


@pytest.mark.asyncio
async def test_child_run_is_nested_under_the_delegating_tool_call():
    _, _, trajectory = await _run(await _parent())

    [delegation] = trajectory["steps"][1]["tool_calls"]
    assert delegation["provider"] == "subagent"
    child = delegation["subagent"]["child_trajectory"]
    assert child["trace_id"] == delegation["subagent"]["child_trace_id"]
    assert child["parent_trace_id"] == trajectory["trace_id"]
    assert [c["outcome"] for step in child["steps"] for c in step["tool_calls"]] == ["success"]
    assert child["final"]["output"]["response"] == "child done"


@pytest.mark.asyncio
async def test_every_event_is_accounted_for_exactly_once():
    agent = await _parent()
    _, trace, trajectory = await _run(agent)

    child_trajectory = trajectory["steps"][1]["tool_calls"][0]["subagent"]["child_trajectory"]
    child_trace = await agent.telemetry_store.get_trace(child_trajectory["trace_id"])
    for record, built in ((trace, trajectory), (child_trace, child_trajectory)):
        assert set(trajectory_event_ids(built)) == {e.event_id for e in record.events}


@pytest.mark.asyncio
async def test_trajectory_from_a_reloaded_file_is_identical():
    agent = await _parent()
    result, trace, trajectory = await _run(agent)

    reloaded = await JsonlTelemetryStore(agent.telemetry_store.path).get_trace(
        result["trace_id"]
    )
    rebuilt = build_trajectory(
        reloaded,
        children={
            call["subagent"]["child_trace_id"]: call["subagent"]["child_trajectory"]
            for step in trajectory["steps"]
            for call in step["tool_calls"]
            if call["subagent"]
        },
    )
    assert json.dumps(rebuilt, sort_keys=True, default=str) == json.dumps(
        trajectory, sort_keys=True, default=str
    )


@pytest.mark.asyncio
async def test_default_capture_keeps_structure_and_states_what_is_missing():
    _, trace, trajectory = await _run(await _parent(TelemetryConfig()))

    first = trajectory["steps"][0]
    assert first["model_calls"][0]["response"] is None
    assert first["model_calls"][0]["response_capture"]["state"] == "not_recorded"
    assert first["model_calls"][0]["facts"]["finish_reason"] == "tool_calls"
    assert [c["outcome"] for c in first["tool_calls"]] == ["success", "error", "rejected"]
    assert trajectory["evidence_status"] == "partial"
    assert trajectory["capture_gaps"]
    assert set(trajectory_event_ids(trajectory)) == {e.event_id for e in trace.events}


@pytest.mark.asyncio
async def test_trajectory_by_run_id_and_unknown_run():
    agent = await _parent()
    result, _, by_trace = await _run(agent)

    by_run = await agent.get_trajectory(run_id=result["run_id"])
    assert by_run["trace_id"] == by_trace["trace_id"]
    assert by_run["other_trace_ids_for_run"] == []
    assert await agent.get_trajectory(run_id="run_missing") is None


def test_served_trajectory_routes():
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = asyncio.run(_parent())
    client = TestClient(
        OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    )
    response = client.post("/run/sync", json={"query": "go", "session_id": "served-trajectory"})
    run_id = response.json()["run_id"]
    trace_id = response.json()["trace_id"]

    by_run = client.get(f"/telemetry/runs/{run_id}/trajectory")
    by_trace = client.get(f"/telemetry/traces/{trace_id}/trajectory")

    assert by_run.status_code == 200
    assert by_run.json()["trace_id"] == trace_id
    assert by_run.json()["execution_surface"] == "serve"
    assert by_trace.json()["trace_id"] == trace_id
    assert client.get("/telemetry/runs/run_missing/trajectory").status_code == 404
