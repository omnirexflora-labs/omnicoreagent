"""Spawned workers spend the lead's budgets: one ledger for the lead and its
workers, as the sub-agents page promises.

The rc7 security review (S1-2): each worker built its own budgets from a copy
of the lead's policy, keyed on its own run, a new session and its own agent
name, so three workers made nine model calls under a lead limit of four and
the lead's ledger showed two.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent import OmniCoreAgent, ToolRegistry
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.token_usage import Usage

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class Scripted:
    def __init__(self, *turns, count=None):
        self.turns = list(turns)
        self.count = count

    def estimate_cost(self, usage):
        return 0.01

    async def llm_call(self, messages, tools=None, **kwargs):
        if self.count is not None:
            self.count.append(1)
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0) if self.turns else "done"
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(cid, name, json.dumps(args)) for cid, name, args in turn),
            finish_reason="tool_calls",
            usage=usage,
        )

    async def llm_stream(self, messages, tools=None):
        yield {"type": "turn_complete", "turn": await self.llm_call(messages, tools)}


def _lead(tmp_path, budgets):
    return OmniCoreAgent(
        name="lead",
        system_instruction="x",
        model_config=MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_subagents": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {"budgets": budgets},
        },
    )


def _script_workers(lead, worker_calls):
    factory = lead._subagent_factory
    original = factory.create_subagent

    def create(**kw):
        agent = original(**kw)
        name = kw["name"]
        init = agent.initialize

        async def initialize():
            await init()
            agent.llm_connection = Scripted(
                [("a", "write_file", {"path": f"{name}/a.txt", "content": "a"})],
                [("b", "write_file", {"path": f"{name}/out.md", "content": "b"})],
                "worker done",
                count=worker_calls,
            )

        agent.initialize = initialize
        return agent

    factory.create_subagent = create


@pytest.mark.asyncio
async def test_workers_spend_the_leads_budgets(tmp_path):
    budgets = {
        scope: [{"meter": "model_calls", "limit": 4, "on_exhausted": "terminate"}]
        for scope in ("request", "session", "agent")
    }
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [
        {"name": f"w{i}", "role": "r", "task": "t", "output_path": f"w{i}/out.md"}
        for i in range(3)
    ]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    worker_calls: list = []
    _script_workers(lead, worker_calls)
    try:
        result = await lead.run("go", session_id="lead-session")
        status = await lead.budget_status(result["run_id"])
    finally:
        await lead.cleanup()

    # One model call by the lead, then the workers share what is left: no
    # more than four model calls in all, and the lead's ledger shows them.
    assert 1 + len(worker_calls) <= 4 + 1, worker_calls
    spent = {entry["scope"]: entry["spent"] for entry in status if entry["meter"] == "model_calls"}
    assert spent and all(value >= 4 for value in spent.values()), status


@pytest.mark.asyncio
async def test_a_worker_that_runs_out_asks_once_on_the_leads_run(tmp_path):
    budgets = {"request": [{"meter": "model_calls", "limit": 2, "on_exhausted": "pause"}]}
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [{"name": "w0", "role": "r", "task": "t", "output_path": "w0/out.md"}]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    _script_workers(lead, [])
    try:
        result = await lead.run("go", session_id="lead-session")
        record = await lead._run_record(result["run_id"])
    finally:
        await lead.cleanup()

    assert result["status"] == "awaiting_budget", result.get("status")
    pending = [r for r in record.get("budget_requests", []) if r.get("status") == "pending"]
    assert len(pending) == 1, record.get("budget_requests")
