"""The ``run_resumed`` event says why the run was resumed.

The support desk chaos run (2026-10-07) found runs resumed with nothing in
their trace to say whether a person approved a call, a budget was granted, or
a process had died and its lease lapsed. ``run_resumed`` now carries
``cause``, who asked (``trigger``), and, for a lapsed lease, the previous
owner and how long the run sat orphaned.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.runs import resume_cause
from omnicoreagent.core.telemetry.models import TraceFilter

from test_durable_runs_end_to_end import _agent as _approval_agent, _tools as _approval_tools
from test_run_recovery import TURNS, _agent, _crash, _tools
from test_run_suspend import RecordingModel


async def _resumed_event(agent, run_id):
    traces = await agent.telemetry_store.list_traces(TraceFilter(run_id=run_id))
    events = [
        event
        for trace in traces
        for event in (await agent.telemetry_store.get_trace(trace.trace_id)).events
        if event.event_type == "run_resumed"
    ]
    assert len(events) == 1, events
    return events[0].metadata


def test_a_waiting_run_resumes_because_of_its_decision():
    approved = {"status": "awaiting_approval", "approvals": [{"status": "approved"}]}
    assert resume_cause(approved)["cause"] == "approval"
    granted = {"status": "awaiting_budget", "budget_requests": [{"status": "granted"}]}
    assert resume_cause(granted)["cause"] == "budget_grant"
    denied = {"status": "awaiting_budget", "budget_requests": [{"status": "denied"}]}
    assert resume_cause(denied)["cause"] == "budget_denied"
    assert resume_cause({"status": "interrupted"})["cause"] == "interrupted"


def test_a_lapsed_lease_names_the_dead_owner_and_the_orphan_time():
    record = {
        "status": "running",
        "owner": "owner_dead",
        "heartbeat_at": "2026-10-07T10:00:00+00:00",
        "lease_seconds": 60,
    }
    from datetime import datetime, timezone

    now = datetime(2026, 10, 7, 10, 5, 0, tzinfo=timezone.utc)
    cause = resume_cause(record, now=now)
    assert cause["cause"] == "recovered_after_lapsed_lease"
    assert cause["previous_owner"] == "owner_dead"
    assert cause["lease_expired_at"] == "2026-10-07T10:01:00+00:00"
    assert cause["orphaned_seconds"] == 240


@pytest.mark.asyncio
async def test_a_recovered_run_says_it_was_recovered_and_from_whom(tmp_path):
    agent = await _agent(RecordingModel(*TURNS, "recovered"), _tools(tmp_path / "ledger"))
    crashed = await _crash(agent)
    await asyncio.sleep(1.5)

    await agent.resume("run_crash")

    event = await _resumed_event(agent, "run_crash")
    assert event["cause"] == "recovered_after_lapsed_lease"
    assert event["trigger"] == "explicit"
    assert event["previous_owner"] == crashed["owner"]
    assert event["orphaned_seconds"] >= 0.4
    assert event["lease_expired_at"]


@pytest.mark.asyncio
async def test_an_approved_run_says_it_resumed_on_an_approval(tmp_path):
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "invoice sent",
    )
    agent = await _approval_agent(model, _approval_tools(tmp_path / "ledger", {"armed": False}))
    paused = await agent.run("draft and send the invoice", session_id="b", run_id="run_s1")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_s1", approval["approval_id"], decision="approve", approver="alice")

    await agent.resume("run_s1")

    event = await _resumed_event(agent, "run_s1")
    assert event["cause"] == "approval"
    assert event["trigger"] == "explicit"
    assert "previous_owner" not in event
