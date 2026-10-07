"""An agent finds the runs its dead processes left, and takes each over once.

The support desk chaos run (2026-10-07): runs whose process died (`docker
kill`) or whose store dropped stayed `running` with a lapsed lease until a
person called `resume(run_id)`. A server now sweeps for them. A run is taken
only by the version-checked claim the record already uses, so two sweepers never
both resume one; a run waiting for a person is never touched.
"""

from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.core.runs import update_from_outside
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry.models import TraceFilter

from test_execute_tool import _MODEL
from test_run_recovery import TURNS, _agent as _crashing_agent, _crash, _tools
from test_run_suspend import RecordingModel


async def _survivor(router, model, tools, **config):
    """Another process of the same agent, sharing the store."""
    agent = OmniCoreAgent(
        name="recoverable",
        system_instruction="Do the work.",
        model_config=_MODEL,
        local_tools=tools,
        memory_router=router,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "run_lease_seconds": 1,
            **config,
        },
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


async def _orphan(tmp_path, run_id="run_orphan"):
    model = RecordingModel(*TURNS, "recovered")
    agent = await _crashing_agent(model, _tools(tmp_path / "ledger"))
    await _crash(agent, run_id=run_id)
    return agent


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
async def test_a_run_whose_owner_died_is_claimed_and_finishes(tmp_path):
    dead = await _orphan(tmp_path)
    crashed = await dead.get_run("run_orphan")
    survivor = await _survivor(
        dead.memory_router, RecordingModel("recovered"), _tools(tmp_path / "ledger")
    )
    await asyncio.sleep(1.3)

    claims = await survivor.claim_orphaned_runs()

    assert [claim["run_id"] for claim in claims] == ["run_orphan"]
    result = await survivor.resume_claimed(claims[0])
    assert result["status"] == "success"
    run = await survivor.get_run("run_orphan")
    assert run["status"] == "completed"
    event = await _resumed_event(survivor, "run_orphan")
    assert event["trigger"] == "orphan_sweep"
    assert event["cause"] == "recovered_after_lapsed_lease"
    assert event["previous_owner"] == crashed["owner"]
    assert event["orphaned_seconds"] >= 0.2


@pytest.mark.asyncio
async def test_a_live_run_is_not_claimed(tmp_path):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(
        dead.memory_router,
        RecordingModel("x"),
        _tools(tmp_path / "ledger"),
        run_lease_seconds=60,
    )

    # The dead process's lease is 1 second, but the record says 60 once the
    # heartbeat is fresh: nothing has lapsed yet.
    await update_from_outside(
        survivor.memory_router,
        "run_orphan",
        lambda record: record.update(lease_seconds=60),
    )

    assert await survivor.claim_orphaned_runs() == []


@pytest.mark.asyncio
async def test_two_sweepers_never_both_claim_one_run(tmp_path):
    dead = await _orphan(tmp_path)
    first = await _survivor(dead.memory_router, RecordingModel("a"), _tools(tmp_path / "l1"))
    second = await _survivor(dead.memory_router, RecordingModel("b"), _tools(tmp_path / "l2"))
    await asyncio.sleep(1.3)

    claims = await asyncio.gather(first.claim_orphaned_runs(), second.claim_orphaned_runs())

    assert sum(len(claimed) for claimed in claims) == 1


@pytest.mark.asyncio
async def test_a_claimed_run_is_not_claimed_again_while_its_new_owner_works(tmp_path):
    dead = await _orphan(tmp_path)
    first = await _survivor(
        dead.memory_router, RecordingModel("a"), _tools(tmp_path / "l1"), run_lease_seconds=60
    )
    second = await _survivor(
        dead.memory_router, RecordingModel("b"), _tools(tmp_path / "l2"), run_lease_seconds=60
    )
    await update_from_outside(
        first.memory_router, "run_orphan", lambda record: record.update(lease_seconds=1)
    )
    await asyncio.sleep(1.3)

    assert len(await first.claim_orphaned_runs()) == 1
    # The claim refreshed the heartbeat under the claimer's lease.
    assert await second.claim_orphaned_runs() == []


@pytest.mark.asyncio
async def test_a_run_waiting_for_a_person_is_left_alone(tmp_path):
    import json

    from test_durable_runs_end_to_end import _agent as _approval_agent, _tools as _approval_tools

    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "sent",
    )
    agent = await _approval_agent(model, _approval_tools(tmp_path / "ledger", {"armed": False}))
    paused = await agent.run("draft and send", session_id="b", run_id="run_wait")
    assert paused["status"] == "awaiting_approval"
    await asyncio.sleep(1.3)

    assert await agent.claim_orphaned_runs() == []
    assert (await agent.get_run("run_wait"))["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_a_background_run_is_left_to_its_supervisor(tmp_path):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(dead.memory_router, RecordingModel("x"), _tools(tmp_path / "l"))
    await update_from_outside(
        survivor.memory_router, "run_orphan", lambda record: record.update(surface="background")
    )
    await asyncio.sleep(1.3)

    assert await survivor.claim_orphaned_runs() == []


@pytest.mark.asyncio
async def test_another_agents_runs_are_not_claimed(tmp_path):
    dead = await _orphan(tmp_path)
    other = await _survivor(dead.memory_router, RecordingModel("x"), _tools(tmp_path / "l"))
    other.name = "someone_else"
    await asyncio.sleep(1.3)

    assert await other.claim_orphaned_runs() == []


@pytest.mark.asyncio
async def test_a_run_that_keeps_killing_its_process_is_given_up_on(tmp_path):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(dead.memory_router, RecordingModel("x"), _tools(tmp_path / "l"))

    for attempt in range(3):
        await asyncio.sleep(1.2)
        claims = await survivor.claim_orphaned_runs(max_recoveries=3)
        assert len(claims) == 1, attempt
        # The process dies right after claiming: its heartbeat stops with it.
        await survivor.release_claim(claims[0])
    await asyncio.sleep(1.2)

    assert await survivor.claim_orphaned_runs(max_recoveries=3) == []
    run = await survivor.get_run("run_orphan")
    assert run["status"] == "failed"
    assert "recovered 3 times" in run["error"]["message"]
