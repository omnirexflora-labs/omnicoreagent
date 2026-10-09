"""A run whose person decided, and whose client never resumed it, is resumed.

The support desk ramp at 100 users (2026-10-07): the admission limit answered
some `POST /runs/{id}/resume` with 503, the client did not retry, and 119 runs
sat in `awaiting_approval` with the approval already `approved`. The sweep
skipped them because they were waiting for a person, and the person had
answered. A decided run whose grace period has passed is now claimed through
the same version-checked claim, and a client's own resume that arrives at the
same moment loses cleanly instead of running the tool twice.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.telemetry.models import TraceFilter
from test_budget_enforcement import PricedModel, _agent as _budget_agent
from test_durable_runs_end_to_end import _agent, _tools
from test_run_suspend import RecordingModel

GRACE = 0.3
TURNS = [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))]


async def _paused(tmp_path, run_id="run_decided"):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(TURNS, "sent"), _tools(ledger, {"armed": False}))
    paused = await agent.run("draft and send", session_id="desk", run_id=run_id)
    assert paused["status"] == "awaiting_approval"
    return agent, paused, ledger


async def _sweeper(first, tmp_path):
    """A second process of the agent, sharing the store: the server's sweep."""
    return await _agent(
        RecordingModel("sent"),
        _tools(tmp_path / "ledger", {"armed": False}),
        memory_router=first.memory_router,
    )


async def _decide(agent, paused, decision):
    (approval,) = paused["approvals"]
    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision=decision, approver="alice"
    )


async def _resumed_event(agent, run_id):
    traces = await agent.telemetry_store.list_traces(TraceFilter(run_id=run_id))
    events = [
        event
        for trace in traces
        for event in (await agent.telemetry_store.get_trace(trace.trace_id)).events
        if event.event_type == "run_resumed"
    ]
    return events[-1].metadata


@pytest.mark.asyncio
async def test_an_approved_run_nobody_resumed_is_resumed_by_the_sweep(tmp_path):
    agent, paused, ledger = await _paused(tmp_path)
    await _decide(agent, paused, "approve")
    sweeper = await _sweeper(agent, tmp_path)

    # Inside the grace period the client may still be about to resume.
    assert await sweeper.claim_orphaned_runs(decided_grace_seconds=GRACE) == []
    await asyncio.sleep(GRACE + 0.1)
    claims = await sweeper.claim_orphaned_runs(decided_grace_seconds=GRACE)

    assert [claim["run_id"] for claim in claims] == ["run_decided"]
    result = await sweeper.resume_claimed(claims[0])
    assert result["status"] == "success"
    assert (await sweeper.get_run("run_decided"))["status"] == "completed"
    assert ledger.read_text().splitlines() == ["draft", "sent INV-1"], "the refund ran once"
    event = await _resumed_event(sweeper, "run_decided")
    assert event["trigger"] == "orphan_sweep"
    assert event["cause"] == "approval"
    assert event["decision"] == "approved"
    # Nothing is left to sweep.
    assert await sweeper.claim_orphaned_runs(decided_grace_seconds=0) == []


@pytest.mark.asyncio
async def test_a_denied_run_nobody_resumed_completes_as_denied(tmp_path):
    agent, paused, ledger = await _paused(tmp_path)
    await _decide(agent, paused, "deny")
    sweeper = await _sweeper(agent, tmp_path)
    await asyncio.sleep(GRACE + 0.1)

    claims = await sweeper.claim_orphaned_runs(decided_grace_seconds=GRACE)
    await sweeper.resume_claimed(claims[0])

    run = await sweeper.get_run("run_decided")
    assert run["status"] == "completed"
    assert run["approvals"][0]["decision"] == "deny"
    assert ledger.read_text().splitlines() == ["draft"], "a denied call never runs"
    assert (await _resumed_event(sweeper, "run_decided"))["decision"] == "denied"


@pytest.mark.asyncio
async def test_a_run_with_a_decision_still_pending_is_left_alone(tmp_path):
    agent, _, _ = await _paused(tmp_path)
    sweeper = await _sweeper(agent, tmp_path)
    await asyncio.sleep(GRACE + 0.1)

    assert await sweeper.claim_orphaned_runs(decided_grace_seconds=0) == []
    assert (await sweeper.get_run("run_decided"))["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_a_granted_budget_nobody_resumed_is_resumed_by_the_sweep():
    call = ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),))
    one_call = {"request": [{"meter": "tool_calls", "limit": 1}]}
    agent = await _budget_agent(PricedModel(call, call, ModelTurn(content="done")), budgets=one_call)
    waiting = await agent.run("go", session_id="desk")
    assert waiting["status"] == "awaiting_budget"
    await asyncio.sleep(GRACE + 0.1)
    # Still undecided: left alone.
    assert await agent.claim_orphaned_runs(decided_grace_seconds=0) == []

    await agent.grant_budget(waiting["run_id"], amount=3, approver="ops@example.com")
    await asyncio.sleep(GRACE + 0.1)
    claims = await agent.claim_orphaned_runs(decided_grace_seconds=GRACE)

    assert [claim["run_id"] for claim in claims] == [waiting["run_id"]]
    assert claims[0]["cause"]["cause"] == "budget_grant"
    result = await agent.resume_claimed(claims[0])
    assert result["status"] == "success", result


@pytest.mark.asyncio
async def test_a_sweep_that_races_the_clients_resume_runs_the_tool_once(tmp_path):
    agent, paused, ledger = await _paused(tmp_path)
    await _decide(agent, paused, "approve")
    sweeper = await _sweeper(agent, tmp_path)
    await asyncio.sleep(GRACE + 0.1)

    # The client's resume has read the record and is on its way to its first
    # save; the sweep claims the run in that gap.
    reached, go = asyncio.Event(), asyncio.Event()
    warm_up = agent._warm_up_model_client

    async def held(**kwargs):
        reached.set()
        await go.wait()
        return await warm_up(**kwargs)

    agent._warm_up_model_client = held
    client = asyncio.create_task(agent.resume("run_decided"))
    await reached.wait()
    claims = await sweeper.claim_orphaned_runs(decided_grace_seconds=GRACE)
    assert len(claims) == 1
    go.set()

    # The loser is told the run is taken (the route answers 409), not failed.
    with pytest.raises(ValueError, match="another process"):
        await client
    result = await sweeper.resume_claimed(claims[0])

    assert result["status"] == "success"
    assert ledger.read_text().splitlines() == ["draft", "sent INV-1"], "the refund ran once"
    run = await sweeper.get_run("run_decided")
    assert run["status"] == "completed", "the loser did not overwrite the winner's record"


@pytest.mark.asyncio
async def test_a_client_resume_that_wins_leaves_the_sweep_nothing(tmp_path):
    agent, paused, ledger = await _paused(tmp_path)
    await _decide(agent, paused, "approve")
    sweeper = await _sweeper(agent, tmp_path)
    await asyncio.sleep(GRACE + 0.1)

    await agent.resume("run_decided")

    assert await sweeper.claim_orphaned_runs(decided_grace_seconds=0) == []
    assert ledger.read_text().splitlines() == ["draft", "sent INV-1"]
    with pytest.raises(ValueError):
        await sweeper.resume("run_decided")
