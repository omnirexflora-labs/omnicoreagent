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
async def test_a_granted_worker_continues_when_the_lead_resumes(tmp_path):
    # The rc7 gate (F): a worker that ran out of budget was parked, the lead
    # paused on its own next call, and after the grant the lead resumed
    # without the worker and reported success with the work undone. The
    # worker's request is mirrored on the lead's run, the grant reaches it,
    # and the lead's delegation resumes the worker.
    budgets = {"request": [{"meter": "model_calls", "limit": 2, "on_exhausted": "pause"}]}
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [{"name": "w0", "role": "r", "task": "t", "output_path": "w0/out.md"}]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    worker_calls: list = []
    _script_workers(lead, worker_calls)
    try:
        paused = await lead.run("go", session_id="lead-session")
        assert paused["status"] == "awaiting_budget", paused.get("status")
        request = paused["budget_request"]
        (waiting,) = request["delegated"]
        assert waiting["name"] == "w0" and waiting["run_id"]
        worker_before = await lead.get_run(waiting["run_id"])
        assert worker_before["status"] == "awaiting_budget"

        await lead.grant_budget(paused["run_id"], amount=10, approver="bob")
        result = await lead.resume(paused["run_id"])
        worker_after = await lead.get_run(waiting["run_id"])
        status = await lead.budget_status(paused["run_id"])
    finally:
        await lead.cleanup()

    assert result["status"] == "success" and result["response"] == "all done"
    assert worker_after["status"] == "completed", "the same worker finished, resumed"
    assert (tmp_path / "ws" / "files" / "w0" / "out.md").read_text() == "b"
    assert len(worker_after["trace_ids"]) == 2, "the worker's run resumed, it did not start over"
    spent = {e["scope"]: e["spent"] for e in status if e["meter"] == "model_calls"}
    # lead 1, worker 1, then (the scripted model replays from its first
    # turn on the resumed worker) worker 3, lead 1: every call on one ledger.
    assert spent["request"] == 1 + len(worker_calls) + 1, status


@pytest.mark.asyncio
async def test_every_worker_stopped_by_one_budget_resumes(tmp_path):
    # The rc8 gate (B7-2): two workers stopped by the same budget merged into
    # one request that named only the first; on resume the second started
    # over and repeated its first step.
    budgets = {"request": [{"meter": "model_calls", "limit": 3, "on_exhausted": "pause"}]}
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [{"name": f"w{i}", "role": "r", "task": "t", "output_path": f"w{i}/out.md"} for i in range(2)]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    worker_calls: list = []
    _script_workers(lead, worker_calls)
    try:
        paused = await lead.run("go", session_id="lead-session")
        assert paused["status"] == "awaiting_budget", paused.get("status")
        record = await lead.get_run(paused["run_id"])
        (request,) = [r for r in record["budget_requests"] if r["status"] == "pending"]
        assert {d["name"] for d in request["delegated"]} == {"w0", "w1"}, request
        await lead.grant_budget(paused["run_id"], amount=20, approver="bob")
        result = await lead.resume(paused["run_id"])
        runs = [await lead.get_run(d["run_id"]) for d in request["delegated"]]
    finally:
        await lead.cleanup()

    assert result["status"] == "success"
    assert [r["status"] for r in runs] == ["completed", "completed"]
    assert all(len(r["trace_ids"]) == 2 for r in runs), "each worker resumed, none started over"
    for i in range(2):
        assert (tmp_path / "ws" / "files" / f"w{i}" / "out.md").read_text() == "b"


@pytest.mark.asyncio
async def test_a_tool_calls_grant_of_the_shortfall_lets_the_worker_finish(tmp_path):
    # The rc8 gate (C): the lead's spawn call, run again to continue its
    # parked worker, was charged as a new tool call and spent the grant the
    # worker was waiting for; granting the shortfall never converged.
    budgets = {"request": [{"meter": "tool_calls", "limit": 2, "on_exhausted": "pause"}]}
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [{"name": "w0", "role": "r", "task": "t", "output_path": "w0/out.md"}]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    # One script for the worker across its pause, as a real model's turns
    # continue: a fresh script per creation replays the first turn.
    worker_model = Scripted(
        [("a", "write_file", {"path": "w0/a.txt", "content": "a"})],
        [("b", "write_file", {"path": "w0/out.md", "content": "b"})],
        "worker done",
    )
    factory = lead._subagent_factory
    original = factory.create_subagent

    def create(**kw):
        agent = original(**kw)
        init = agent.initialize

        async def initialize():
            await init()
            agent.llm_connection = worker_model

        agent.initialize = initialize
        return agent

    factory.create_subagent = create
    try:
        result = await lead.run("go", session_id="lead-session")
        rounds = 0
        while result["status"] == "awaiting_budget" and rounds < 4:
            rounds += 1
            await lead.grant_budget(result["run_id"], approver="bob")  # the shortfall
            result = await lead.resume(result["run_id"])
    finally:
        await lead.cleanup()

    assert result["status"] == "success", (result.get("status"), rounds)
    assert rounds == 1, f"one grant of the shortfall is enough; took {rounds}"
    assert (tmp_path / "ws" / "files" / "w0" / "out.md").read_text() == "b"


@pytest.mark.asyncio
async def test_spawning_past_a_subagent_runs_limit_ends_the_run(tmp_path):
    # The rc7 gate (B7-6): the subagent_runs charge happens inside the spawn
    # tool, so its refusal came back as a tool error and the run went on to
    # end "success"; model_calls and tool_calls refusals end the run.
    budgets = {"request": [{"meter": "subagent_runs", "limit": 2, "on_exhausted": "terminate"}]}
    lead = _lead(tmp_path, budgets)
    await lead.initialize()
    workers = [{"name": f"w{i}", "role": "r", "task": "t", "output_path": f"w{i}/out.md"} for i in range(3)]
    lead.llm_connection = Scripted([("s1", "spawn_subagents", {"subagents": workers})], "all done")
    _script_workers(lead, [])
    try:
        result = await lead.run("go", session_id="lead-session")
    finally:
        await lead.cleanup()

    assert result["status"] == "error", result.get("status")
    assert "subagent_runs" in str(result)


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
