"""A resumed run's evidence is whole (stranger test S1, 2026-09-26).

An agent building a refund desk from the docs alone found that the refund a
person approved ran on resume but was listed outside every step, so each
documented loop over `steps` missed it; that its decision read as a rule's
`allow`; that the training record reported it as still waiting; and that the
run story's `including_subagents` totals held only the last segment.
"""

from __future__ import annotations

import json

import pytest

from test_durable_runs_end_to_end import _agent, _tools
from test_run_suspend import RecordingModel


async def _approved_and_resumed(tmp_path):
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "invoice sent",
    )
    agent = await _agent(model, _tools(tmp_path / "ledger", {"armed": False}))
    paused = await agent.run("draft and send the invoice", session_id="b", run_id="run_s1")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_s1", approval["approval_id"], decision="approve", approver="alice")
    await agent.resume("run_s1")
    return agent


@pytest.mark.asyncio
async def test_the_approved_call_is_in_the_step_that_asked_for_it(tmp_path):
    agent = await _approved_and_resumed(tmp_path)
    story = await agent.get_run_trajectory("run_s1")
    resumed = story["segments"][1]["trajectory"]

    assert resumed["tool_calls_outside_steps"] == []
    first, *rest = resumed["steps"]
    assert first["resumed"] is True and first["step"] == 1, "the step that paused, continued"
    (call,) = first["tool_calls"]
    assert (call["tool_name"], call["outcome"]) == ("send_invoice", "success")
    assert [step["step"] for step in rest] == [2], "numbering continues across segments"


@pytest.mark.asyncio
async def test_the_decision_names_the_person_who_approved(tmp_path):
    agent = await _approved_and_resumed(tmp_path)
    story = await agent.get_run_trajectory("run_s1")
    (call,) = story["segments"][1]["trajectory"]["steps"][0]["tool_calls"]

    decision = call["governance"][-1]
    assert decision["effect"] == "allow" and decision["reason_code"] == "approved"
    assert decision["approved_by"] == "alice" and decision["approval_id"]


@pytest.mark.asyncio
async def test_the_story_totals_include_every_segment(tmp_path):
    agent = await _approved_and_resumed(tmp_path)
    totals = (await agent.get_run_trajectory("run_s1"))["totals"]

    assert totals["including_subagents"]["tokens"]["total"] == totals["tokens"]["total"]


@pytest.mark.asyncio
async def test_the_training_record_has_the_call_that_ran(tmp_path):
    agent = await _approved_and_resumed(tmp_path)
    (record,) = await agent.training_records(run_id="run_s1")

    calls = [(c["name"], c["outcome"]) for step in record["steps"] for c in step["tool_calls"]]
    assert ("send_invoice", "success") in calls


@pytest.mark.asyncio
async def test_another_process_sees_what_it_is_approving(tmp_path):
    """The refund desk's approver ran in a second process and printed
    `issue_refund None`: `get_run` gave the arguments' digest, not the
    arguments, while `GET /runs/{run_id}` gave them."""
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "invoice sent",
    )
    first = await _agent(model, _tools(tmp_path / "ledger", {"armed": False}))
    await first.run("draft and send the invoice", session_id="b", run_id="run_s2")

    second = await _agent(model, _tools(tmp_path / "ledger", {"armed": False}), memory_router=first.memory_router)
    (approval,) = (await second.get_run("run_s2"))["approvals"]
    assert approval["tool_name"] == "send_invoice"
    assert approval["arguments"] == {"invoice": "INV-1"}
