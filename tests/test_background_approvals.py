"""Durable runs, D6: a background run that asks for approval waits for it.

The agent pauses for approval; the background run goes to `awaiting_approval`
(not `completed`), holds its task's slot, and keeps no lease. After a person
decides, `manager.resume_run(run_id)` queues it again, and the next attempt
continues the same durable run (same run ID) instead of starting over.
"""

from __future__ import annotations

import pytest

from omnicoreagent.background import BackgroundAgentManager, RunStatus
from omnicoreagent.core.workspace.manager import Workspace
from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent, _file


def _manager(tmp_path):
    workspace = Workspace.from_config(
        {"workspace_backend": "local", "workspace_dir": str(tmp_path / "bg")}
    ).ensure()
    return BackgroundAgentManager(task_store="in_memory", workspace=workspace, lease_seconds=30)


async def _setup(tmp_path, *turns):
    agent = await _agent(tmp_path, RecordingModel(*turns))
    manager = _manager(tmp_path)
    await manager.register_agent("keeper", agent)
    await manager.register_task(task_id="tidy", agent_id="keeper", query="tidy up", schedule={"type": "manual"})
    return agent, manager


@pytest.mark.asyncio
async def test_a_background_run_waits_for_approval_and_resumes(tmp_path):
    agent, manager = await _setup(tmp_path, WRITE_AND_DELETE, DELETE, "cleaned up")

    waiting = await manager.run_now("tidy", wait=True, timeout_seconds=15)

    assert waiting.status == RunStatus.AWAITING_APPROVAL
    assert waiting.lease_token is None
    assert _file(tmp_path, "old.txt").exists()
    events = [e["event"] for e in await manager.get_run_events(waiting.run_id)]
    assert "background_run_awaiting_approval" in events
    (approval,) = (await agent.get_run(waiting.run_id))["approvals"]

    await agent.resolve_approval(waiting.run_id, approval["approval_id"], decision="approve", approver="alice")
    requeued = await manager.resume_run(waiting.run_id)
    assert requeued.status == RunStatus.QUEUED
    finished = await manager.run_until_terminal(waiting.run_id, timeout_seconds=15)

    assert finished.status == RunStatus.COMPLETED
    assert not _file(tmp_path, "old.txt").exists()
    record = await agent.get_run(waiting.run_id)
    assert record["status"] == "completed" and len(record["trace_ids"]) == 2


@pytest.mark.asyncio
async def test_only_a_waiting_background_run_can_be_resumed_and_it_can_be_cancelled(tmp_path):
    agent, manager = await _setup(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    waiting = await manager.run_now("tidy", wait=True, timeout_seconds=15)

    await manager.cancel_run(waiting.run_id)
    cancelled = await manager.get_run(waiting.run_id)

    assert cancelled.status == RunStatus.CANCELLED
    with pytest.raises(ValueError, match="awaiting_approval"):
        await manager.resume_run(waiting.run_id)


# --- a run that runs out of budget waits the same way ---------------------------


@pytest.mark.asyncio
async def test_a_background_run_waits_for_budget_and_resumes_after_a_top_up(tmp_path):
    """Found by P4 of the proving plan: a background run whose agent paused
    for budget was recorded as *completed* ("Waiting for budget ..." as its
    answer), because the supervisor only knew the approval pause."""
    from test_budget_enforcement import _agent as _budget_agent
    from test_budget_pause import ONE_CALL, _two_turns

    model = _two_turns()
    agent = await _budget_agent(model, budgets=ONE_CALL)
    manager = _manager(tmp_path)
    await manager.register_agent("payer", agent)
    await manager.register_task(task_id="spend", agent_id="payer", query="go", schedule={"type": "manual"})

    waiting = await manager.run_now("spend", wait=True, timeout_seconds=15)

    assert waiting.status == RunStatus.AWAITING_BUDGET
    assert waiting.lease_token is None
    assert "model_calls" in (waiting.result_preview or "")
    events = [e["event"] for e in await manager.get_run_events(waiting.run_id)]
    assert "background_run_awaiting_budget" in events
    assert (await agent.get_run(waiting.run_id))["status"] == "awaiting_budget"

    await agent.grant_budget(waiting.run_id, amount=2, approver="ops", note="finish this one")
    requeued = await manager.resume_run(waiting.run_id)
    assert requeued.status == RunStatus.QUEUED
    finished = await manager.run_until_terminal(waiting.run_id, timeout_seconds=15)

    assert finished.status == RunStatus.COMPLETED
    assert model.calls == 2, "the run carried on rather than starting again"
    record = await agent.get_run(waiting.run_id)
    assert record["status"] == "completed" and len(record["trace_ids"]) == 2
