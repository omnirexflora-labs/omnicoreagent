from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import SpanStatus, TelemetryConfig
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


class ScriptedModel:
    """Requests the scripted tool calls on the first turn, then answers."""

    def __init__(self, *calls: tuple[str, str, str]) -> None:
        self.calls = calls
        self.turns = 0

    async def llm_call(self, messages, tools=None, **kwargs):
        self.turns += 1
        if self.turns == 1:
            return ModelTurn(
                tool_calls=tuple(ToolRequest(*call) for call in self.calls),
                finish_reason="tool_calls",
            )
        return "done"


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 1}

    @tools.register_tool("slow_lookup", description="Look up slowly.")
    async def slow_lookup(key: str) -> dict:
        await asyncio.sleep(5)
        return {"key": key}

    return tools


def _governance(capability="tool.local.call"):
    return {
        "enabled": True,
        "policy": {
            "name": "tool-record-policy",
            "mode": "strict",
            "rules": {"allow": [{"rule_id": "allow_local", "capability": capability}]},
        },
    }


async def _agent(model, *, sub_agents=None, telemetry_config=None, **config):
    agent = OmniCoreAgent(
        name="tool-agent",
        system_instruction="You are a tool probe.",
        model_config=_MODEL,
        local_tools=_tools(),
        sub_agents=sub_agents,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **config},
        telemetry_config=telemetry_config,
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


async def _trace(agent, query="go"):
    result = await agent.run(query, session_id="tool-record")
    return await agent.telemetry_store.get_trace(result["trace_id"])


def _events(trace, event_type):
    return [event for event in trace.events if event.event_type == event_type]


@pytest.mark.asyncio
async def test_malformed_arguments_keep_the_raw_string_and_the_reason():
    raw = '{"key": "a", '
    agent = await _agent(ScriptedModel(("call_bad", "lookup", raw)))
    trace = await _trace(agent)

    [requested] = _events(trace, "tool_requested")
    assert requested.input["raw_arguments"] == raw
    assert requested.input["arguments"] is None
    assert requested.output["status"] == "rejected"
    assert requested.output["rejection_reason"] == "invalid_arguments"
    assert requested.output["error_type"]
    assert requested.metadata["rejection_reason"] == "invalid_arguments"
    assert _events(trace, "tool_resolved") == []


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_with_its_own_reason():
    agent = await _agent(ScriptedModel(("call_ghost", "no_such_tool", "{}")))
    trace = await _trace(agent)

    [requested] = _events(trace, "tool_requested")
    assert requested.output["rejection_reason"] == "unknown_tool"
    assert requested.input["raw_arguments"] == "{}"


@pytest.mark.asyncio
async def test_tool_timeout_is_recorded_as_timeout_not_cancel():
    agent = await _agent(ScriptedModel(("call_slow", "slow_lookup", '{"key": "a"}')))
    agent.agent.tool_call_timeout = 0.05
    trace = await _trace(agent)

    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.status == SpanStatus.TIMEOUT
    [error] = _events(trace, "tool_error")
    assert error.error.type == "TimeoutError"
    assert error.metadata["phase"] == "timeout"
    [observation] = _events(trace, "tool_observation")
    content = json.loads(observation.output["message"]["content"])
    assert content["error_type"] == "timeout"


@pytest.mark.asyncio
async def test_cancelled_tool_stays_cancelled():
    agent = await _agent(ScriptedModel(("call_slow", "slow_lookup", '{"key": "a"}')))
    run = asyncio.create_task(agent.run("go", session_id="tool-cancel"))
    for _ in range(200):
        traces = await agent.telemetry_store.list_traces()
        if any(s.kind == "tool.call" for t in traces for s in t.spans):
            break
        await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    [trace] = await agent.telemetry_store.list_traces()
    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.status == SpanStatus.CANCELLED


@pytest.mark.asyncio
async def test_tool_records_link_back_to_the_model_turn_and_resolution():
    agent = await _agent(ScriptedModel(("call_1", "lookup", '{"key": "a"}')))
    trace = await _trace(agent)
    event_ids = {event.event_id for event in trace.events}
    [requested] = _events(trace, "tool_requested")
    [resolved] = _events(trace, "tool_resolved")
    # The tool call was requested by the first of the two model turns.
    response = _events(trace, "model_response")[0]

    for event_type in ("tool_call", "tool_result"):
        [event] = _events(trace, event_type)
        links = event.metadata
        assert links["tool_call_id"] == "call_1"
        assert links["tool_requested_event_id"] == requested.event_id
        assert links["tool_resolved_event_id"] == resolved.event_id
        assert links["model_response_event_id"] == response.event_id
        assert links["model_call_event_id"] in event_ids
        assert links["batch_id"] == requested.metadata["batch_id"]


@pytest.mark.asyncio
async def test_governance_records_redacted_arguments_and_links_decisions():
    agent = await _agent(
        ScriptedModel(("call_1", "lookup", '{"key": "secret-value"}')),
        governance_config=_governance(),
    )
    trace = await _trace(agent)

    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.input["tool_args"] == {"key": "[REDACTED]"}
    decisions = _events(trace, "policy_decision_allow")
    requests = _events(trace, "policy_request_created")
    assert decisions and requests
    for event in [*requests, *decisions]:
        assert event.metadata["tool_call_id"] == "call_1"
        assert event.metadata["capability"] == "tool.local.call"
        assert event.metadata["request_id"]
    assert decisions[0].metadata["effect"] == "allow"
    # Governance redacts arguments wherever they are recorded; the tool's own
    # output is recorded under the tool result policy.
    [requested] = _events(trace, "tool_requested")
    [call] = _events(trace, "tool_call")
    for record in (requested.input, call.input, span.input, *[r.input for r in requests]):
        assert "secret-value" not in json.dumps(record, default=str)


@pytest.mark.asyncio
async def test_governance_facts_survive_restricted_capture():
    agent = await _agent(
        ScriptedModel(("call_1", "lookup", '{"key": "a"}')),
        governance_config=_governance(),
        telemetry_config=TelemetryConfig(record_inputs=False, record_outputs=False),
    )
    trace = await _trace(agent)

    [decision] = _events(trace, "policy_decision_allow")
    assert decision.output is None
    assert decision.metadata["effect"] == "allow"
    assert decision.metadata["tool_call_id"] == "call_1"


def _child_agent():
    child = OmniCoreAgent(
        name="researcher",
        system_instruction="You research.",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off"},
    )
    child._initialized = True
    child.agent = MagicMock()
    child.agent.run = AsyncMock(return_value="child answer")
    child.mcp_client = None
    child.llm_connection = MagicMock()
    child.memory_router = MagicMock()
    child.memory_router.store_message = AsyncMock()
    child.memory_router.get_messages = AsyncMock(return_value=[])
    return child


@pytest.mark.asyncio
async def test_subagent_calls_report_the_subagent_provider():
    agent = await _agent(
        ScriptedModel(("call_child", "delegate_researcher", '{"query": "find it"}')),
        sub_agents=[_child_agent()],
    )
    trace = await _trace(agent)

    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.input["tool_provider"] == "subagent"
    [result] = _events(trace, "tool_result")
    assert result.metadata["tool_provider"] == "subagent"


@pytest.mark.asyncio
async def test_delegation_parameters_are_redacted_under_governance():
    agent = await _agent(
        ScriptedModel(
            ("call_child", "delegate_researcher", '{"query": "customer 4471 salary"}')
        ),
        sub_agents=[_child_agent()],
        governance_config={
            "enabled": True,
            "policy": {
                "name": "delegation-policy",
                "mode": "strict",
                "rules": {
                    "allow": [
                        {"rule_id": "allow_local", "capability": "tool.local.call"},
                        {"rule_id": "allow_spawn", "capability": "subagent.spawn"},
                        {"rule_id": "allow_tools", "capability": "tool.*"},
                    ]
                },
            },
        },
    )
    trace = await _trace(agent)

    parent_dump = json.dumps(trace.model_dump(), default=str)
    assert "customer 4471 salary" not in parent_dump
    [delegation] = [s for s in trace.spans if s.kind == "subagent.run"]
    assert delegation.input["parameters"] == {"query": "[REDACTED]", "session_id": "[REDACTED]"} or (
        delegation.input["parameters"] == {"query": "[REDACTED]"}
    )
