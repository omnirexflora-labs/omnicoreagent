"""0.5.1 A3: a tool call is charged once, when it runs.

The 0.5.0 steward runs showed the ``tool_calls`` meter higher than the calls
that were made (44 against 41, and 40 against 37), by the number of approved
GitHub writes. A call paused for approval was charged when it asked, and
charged again when it ran after approval and resume.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.budgets import BudgetScope
from test_budget_enforcement import _request_spent, _usage
from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent, _file


async def _budgeted(tmp_path, model, limit=10):
    return await _agent(tmp_path, model, budgets={"request": [{"meter": "tool_calls", "limit": limit}]})


@pytest.mark.asyncio
async def test_a_call_paused_for_approval_is_charged_once_when_it_runs(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up")
    agent = await _budgeted(tmp_path, model)
    paused = await agent.run("tidy up", session_id="once-1")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success"
    run = await agent.get_run(paused["run_id"])
    ran = [c for c in run["tool_calls"] if c["state"] == "completed"]
    assert len(ran) == 3  # two writes and the approved delete
    assert (await _request_spent(agent, paused["run_id"]))["tool_calls"] == len(ran)


@pytest.mark.asyncio
async def test_a_call_waiting_for_approval_is_not_charged_until_it_runs(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "never")
    agent = await _budgeted(tmp_path, model)

    paused = await agent.run("tidy up", session_id="once-2")

    assert paused["status"] == "awaiting_approval"
    # Two writes ran; the delete is only asking. The run is still open, so its
    # own counter is read live.
    assert (await _usage(agent, BudgetScope.REQUEST, paused["run_id"]))["tool_calls"] == 2


@pytest.mark.asyncio
async def test_a_denied_call_is_never_charged(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "left it")
    agent = await _budgeted(tmp_path, model)
    paused = await agent.run("tidy up", session_id="once-3")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="deny", approver="alice", note="no")
    await agent.resume(paused["run_id"])

    assert (await _request_spent(agent, paused["run_id"]))["tool_calls"] == 2


@pytest.mark.asyncio
async def test_an_approved_call_the_budget_then_refuses_runs_after_a_grant_without_asking_again(tmp_path):
    # Ask, approve, resume, budget refuses (nothing left), grant, resume. The
    # approval already given must carry through the budget pause, and the call
    # is charged once, when it finally runs.
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up")
    agent = await _budgeted(tmp_path, model, limit=2)
    paused = await agent.run("tidy up", session_id="once-4")
    assert paused["status"] == "awaiting_approval"
    (approval,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")

    waiting = await agent.resume(paused["run_id"])
    assert waiting["status"] == "awaiting_budget", waiting
    assert _file(tmp_path, "old.txt").exists(), "the refused call must not have run"

    await agent.grant_budget(paused["run_id"], approver="ops@example.com")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success", result
    assert not _file(tmp_path, "old.txt").exists()
    run = await agent.get_run(paused["run_id"])
    assert len(run["approvals"]) == 1, "the delete was approved once and never asked again"
    assert (await _request_spent(agent, paused["run_id"]))["tool_calls"] == 3
