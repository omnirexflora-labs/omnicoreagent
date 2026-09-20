"""A delegation is not bounded by the parent's per-tool-call timeout.

Found by P3 of the production proving plan: the steward delegated a fix to
a worker that had to clone, install, change, and test — and the parent's
``tool_call_timeout`` (meant for one tool call) cancelled ``spawn_subagents``
after five minutes, killing the worker mid-work; the next worker met the
same fate. A delegation is bounded by the worker's own limits (its step
cap, the run's deadline), or by ``subagent_timeout`` when an application
sets one; never by the timeout for a single tool call.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}
SPAWN = [("s1", "spawn_subagents", json.dumps({"subagents": [
    {"name": "fix", "role": "fixer", "task": "fix it", "output_path": "/workspace/fix/out.md"}
]}))]


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = list(turns)

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn), finish_reason="tool_calls", usage=usage
        )


async def _lead(tmp_path, *, seconds_of_work: float, **config):
    agent = OmniCoreAgent(
        name="lead",
        system_instruction="Delegate.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_subagents": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            **config,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(SPAWN, "done")

    async def slow_worker(specs):
        await asyncio.sleep(seconds_of_work)
        return {"status": "success", "data": {"completed": len(specs)}, "message": "Completed 1/1 subagents successfully"}

    agent._subagent_factory.run_parallel_subagents = slow_worker
    return agent


async def _delegation_result(agent, result):
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    return next(
        e for e in trace.events
        if e.event_type in {"tool_result", "tool_error"}
        and "spawn_subagents" in ((e.output or {}).get("tool_name"), (e.metadata or {}).get("tool_name"))
    )


@pytest.mark.asyncio
async def test_a_worker_outlasts_the_tool_call_timeout(tmp_path):
    agent = await _lead(tmp_path, seconds_of_work=2.5, tool_call_timeout=2)

    started = time.monotonic()
    result = await agent.run("go", session_id="s1")

    assert result["response"] == "done"
    assert time.monotonic() - started >= 2.5
    delegation = await _delegation_result(agent, result)
    assert delegation.event_type == "tool_result", delegation.output


@pytest.mark.asyncio
async def test_an_application_can_still_bound_a_delegation(tmp_path):
    agent = await _lead(tmp_path, seconds_of_work=5, tool_call_timeout=2, subagent_timeout=2)

    started = time.monotonic()
    result = await agent.run("go", session_id="s1")

    assert result["response"] == "done"
    assert time.monotonic() - started < 4.5
    delegation = await _delegation_result(agent, result)
    assert delegation.event_type == "tool_error"
    assert "time limit" in json.dumps(delegation.output) + json.dumps(delegation.error, default=str)


def test_a_delegation_bound_must_be_a_sensible_number():
    with pytest.raises(ValueError, match="subagent_timeout"):
        OmniCoreAgent(
            name="lead", system_instruction="x", model_config=_MODEL,
            agent_config={"enable_subagents": True, "subagent_timeout": 0},
        )
