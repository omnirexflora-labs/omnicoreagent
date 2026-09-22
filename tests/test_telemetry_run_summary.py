from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import TraceFilter, TraceStatus
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _usage(input_tokens, output_tokens):
    return Usage(
        requests=1,
        request_tokens=input_tokens,
        response_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


class ScriptedModel:
    """Plays back turns; each turn is (tool calls or text, input tokens, output tokens)."""

    def __init__(self, *turns) -> None:
        self.turns = list(turns)

    def estimate_cost(self, usage):
        return usage.total_tokens * 1e-6

    async def llm_call(self, messages, tools=None, **kwargs):
        content, input_tokens, output_tokens = self.turns.pop(0)
        usage = _usage(input_tokens, output_tokens)
        if isinstance(content, str):
            return ModelTurn(content=content, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in content),
            finish_reason="tool_calls",
            usage=usage,
        )


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key}

    @tools.register_tool("explode", description="Always fails.")
    def explode() -> dict:
        raise RuntimeError("tool failed")

    @tools.register_tool("slow", description="Never finishes in time.")
    async def slow() -> dict:
        await asyncio.sleep(5)
        return {}

    return tools


async def _agent(model, **config):
    agent = OmniCoreAgent(
        name="summary-agent",
        system_instruction="You are a summary probe.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off", "max_steps": 10, **config},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


async def _run(agent, query="go"):
    result = await agent.run(query, session_id="run-summary")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    final = next(e for e in trace.events if e.event_type == "final_answer")
    return result, trace, final


@pytest.mark.asyncio
async def test_run_summary_totals_a_successful_run():
    agent = await _agent(
        ScriptedModel(
            (
                [
                    ("call_ok", "lookup", '{"key": "a"}'),
                    ("call_err", "explode", "{}"),
                    ("call_bad", "lookup", "{broken"),
                ],
                100,
                20,
            ),
            ("done", 150, 5),
        ),
        enable_workspace_files=False,
    )
    _, trace, final = await _run(agent)
    summary = final.metadata["run_summary"]

    assert summary["steps"] == 2
    assert summary["model_calls"] == {"total": 2, "agent_turn": 2, "context_summary": 0, "failed": 0}
    assert summary["tokens"] == {"input": 250, "output": 25, "total": 275, "cached_input": 0, "reasoning": 0}
    assert summary["estimated_cost_usd"] == pytest.approx(275e-6)
    assert summary["cost_complete"] is True
    assert summary["tool_calls"]["total"] == 3
    assert summary["tool_calls"]["by_outcome"] == {
        "success": 1,
        "error": 1,
        "rejected": 1,
        "timeout": 0,
        "cancelled": 0,
        "denied": 0,
        "awaiting_approval": 0,
    }
    assert summary["model_retries"] == 0
    assert summary["compressions"] == 0
    assert summary["subagents"] == {"count": 0, "child_trace_ids": []}
    assert summary["duration_ms"] > 0
    responses = [e for e in trace.events if e.event_type == "model_response"]
    assert final.metadata["final_model_response_event_id"] == responses[-1].event_id
    root = next(s for s in trace.spans if s.span_id == trace.root_span_id)
    assert root.output["run_summary"] == summary


@pytest.mark.asyncio
async def test_run_summary_counts_timeouts_and_governance_denials():
    agent = await _agent(
        ScriptedModel(
            ([("call_slow", "slow", "{}"), ("call_denied", "explode", "{}")], 10, 1),
            ("done", 10, 1),
        ),
        enable_workspace_files=False,
        governance_config={
            "enabled": True,
            "policy": {
                "name": "summary-policy",
                "mode": "strict",
                "rules": {
                    "allow": [{"rule_id": "allow_slow", "capability": "tool.local.call"}],
                    "deny": [
                        {
                            "rule_id": "deny_explode",
                            "capability": "tool.local.call",
                            "target": {"tool_name": "explode"},
                        }
                    ],
                },
            },
        },
    )
    # Long enough for authorization, far shorter than the slow tool.
    agent.agent.tool_call_timeout = 0.5
    _, _, final = await _run(agent)

    outcomes = final.metadata["run_summary"]["tool_calls"]["by_outcome"]
    assert outcomes["timeout"] == 1
    assert outcomes["denied"] == 1


@pytest.mark.asyncio
async def test_run_summary_lists_workspace_changes():
    agent = await _agent(
        ScriptedModel(
            (
                [
                    (
                        "call_write",
                        "write_file",
                        '{"path": "notes/out.md", "content": "hi", "mode": "create"}',
                    )
                ],
                10,
                1,
            ),
            ("done", 10, 1),
        )
    )
    _, _, final = await _run(agent)

    [change] = final.metadata["run_summary"]["workspace_changes"]
    assert change["tool_call_id"] == "call_write"
    assert change["operation"] == "write"
    assert change["path"] == "notes/out.md"


@pytest.mark.asyncio
async def test_model_failure_result_records_its_totals():
    class FailingModel(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            if not self.turns:
                raise RuntimeError("provider unavailable")
            return await super().llm_call(messages, tools=tools, **kwargs)

    agent = await _agent(
        FailingModel(([("call_ok", "lookup", '{"key": "a"}')], 40, 4)),
        enable_workspace_files=False,
    )
    result, _, final = await _run(agent)

    assert result["status"] == "error"
    summary = final.metadata["run_summary"]
    assert summary["model_calls"]["total"] == 2
    assert summary["model_calls"]["failed"] == 1
    assert summary["tokens"]["total"] == 44
    assert summary["tool_calls"]["by_outcome"]["success"] == 1


@pytest.mark.asyncio
async def test_raising_run_still_records_its_totals():
    agent = await _agent(
        ScriptedModel(([("call_ok", "lookup", '{"key": "a"}')], 40, 4), ("done", 5, 1)),
        enable_workspace_files=False,
    )
    inner_run = agent.agent.run

    async def run_then_fail(**kwargs):
        await inner_run(**kwargs)
        raise RuntimeError("post-processing failed")

    agent.agent.run = run_then_fail
    with pytest.raises(RuntimeError):
        await agent.run("go", session_id="failed-summary")

    [trace] = await agent.telemetry_store.list_traces(TraceFilter(session_id="failed-summary"))
    assert trace.status == TraceStatus.FAILED
    error = next(e for e in trace.events if e.event_type == "runtime_error")
    summary = error.metadata["run_summary"]
    assert summary["model_calls"]["total"] == 2
    assert summary["tokens"]["total"] == 50
    assert summary["tool_calls"]["by_outcome"]["success"] == 1


def _serve(agent):
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    return TestClient(
        OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    )


def test_served_run_records_an_agent_error_as_failed():
    agent = asyncio.run(
        _agent(ScriptedModel(("", 1, 1)), max_steps=1, enable_workspace_files=False)
    )
    client = _serve(agent)

    response = client.post("/run/sync", json={"query": "go", "session_id": "served-error"})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    traces = asyncio.run(agent.telemetry_store.list_traces(TraceFilter(session_id="served-error")))
    serve_trace = next(t for t in traces if t.spans[0].kind == "serve.request")
    assert serve_trace.status == TraceStatus.FAILED


@pytest.mark.asyncio
async def test_cancelled_served_run_does_not_stay_running():
    import httpx

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    class HangingModel:
        async def llm_call(self, messages, tools=None, **kwargs):
            await asyncio.sleep(30)

    agent = await _agent(HangingModel(), enable_workspace_files=False)
    app = OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        request = asyncio.create_task(
            client.post("/run/sync", json={"query": "go", "session_id": "served-cancel"})
        )
        for _ in range(200):
            traces = await agent.telemetry_store.list_traces(
                TraceFilter(session_id="served-cancel")
            )
            if any(s.kind == "model.call" for t in traces for s in t.spans):
                break
            await asyncio.sleep(0.01)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    traces = await agent.telemetry_store.list_traces(TraceFilter(session_id="served-cancel"))
    serve_trace = next(t for t in traces if t.spans[0].kind == "serve.request")
    agent_trace = next(t for t in traces if t.spans[0].kind == "agent.run")
    assert serve_trace.status == TraceStatus.CANCELLED
    assert agent_trace.status == TraceStatus.CANCELLED
