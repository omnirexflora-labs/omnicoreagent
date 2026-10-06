"""0.5.1 B3: after a crash and resume, the dead segment is closed.

Seen on 0.5.0 (a steward run killed after its push): the run's record said
`completed`, but the trace listing showed the killed segment as `running`
with no end, until retention removed it. A resume now closes the run's
earlier segments that never ended, as `interrupted`, ended at their last
event.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.telemetry import TraceFilter
from test_durable_runs_end_to_end import _agent, _tools
from test_run_recovery import ProcessDied
from test_run_suspend import RecordingModel


async def _crashed_and_resumed(tmp_path):
    crash_once = {"armed": True}
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "sent",
    )
    agent = await _agent(model, _tools(tmp_path / "ledger", crash_once))
    paused = await agent.run("send it", session_id="billing", run_id="run_seg")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_seg", approval["approval_id"], decision="approve", approver="alice")
    with pytest.raises(ProcessDied):
        await agent.resume("run_seg")
    await asyncio.sleep(1.2)  # the lease runs out
    survivor = await _agent(model, _tools(tmp_path / "ledger", crash_once), memory_router=agent.memory_router)
    return agent, survivor


@pytest.mark.asyncio
async def test_the_killed_segment_is_interrupted_with_an_end_time_once_the_run_resumes(tmp_path):
    agent, survivor = await _crashed_and_resumed(tmp_path)
    store = agent.telemetry_store

    before = await store.list_traces(TraceFilter(run_id="run_seg"))
    assert [t.status.value for t in sorted(before, key=lambda t: t.started_at)] == ["suspended", "running"]

    result = await survivor.resume("run_seg")
    assert result["response"] == "sent"

    traces = sorted(await store.list_traces(TraceFilter(run_id="run_seg")), key=lambda t: t.started_at)
    assert [t.status.value for t in traces] == ["suspended", "interrupted", "completed"]
    killed = traces[1]
    assert killed.ended_at is not None
    assert killed.ended_at == max(e.timestamp for e in killed.events)
    assert not await store.list_traces(TraceFilter(run_id="run_seg", status="running"))


@pytest.mark.asyncio
async def test_a_segment_that_ended_is_left_as_it_is(tmp_path):
    agent, survivor = await _crashed_and_resumed(tmp_path)
    store = agent.telemetry_store
    suspended = next(t for t in await store.list_traces(TraceFilter(run_id="run_seg")) if t.status.value == "suspended")

    await survivor.resume("run_seg")

    after = await store.get_trace(suspended.trace_id)
    assert after.status.value == "suspended" and after.ended_at == suspended.ended_at
