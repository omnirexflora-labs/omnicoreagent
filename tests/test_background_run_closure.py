"""A background run that ends closes the agent's run with it.

Found on the repository steward's server during P7: a background run
cancelled while it waited for a budget top-up, and one failed with "lease
expired" after its worker died, both left the agent's durable run record
open — one still "awaiting_budget", one still "running" two days later —
and each left its request budget counter in the ledger for good. When the
background layer ends a run the agent did not finish itself, the agent's
record now ends too, saying why, and the request's counters are released.
"""

from __future__ import annotations

import pytest

from omnicoreagent.background import RunStatus
from test_background_approvals import _manager, _setup
from test_run_suspend import DELETE, WRITE_AND_DELETE


@pytest.mark.asyncio
async def test_cancelling_a_parked_background_run_closes_the_agents_run(tmp_path):
    agent, manager = await _setup(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    waiting = await manager.run_now("tidy", wait=True, timeout_seconds=15)
    assert (await agent.get_run(waiting.run_id))["status"] == "awaiting_approval"

    await manager.cancel_run(waiting.run_id)

    record = await agent.get_run(waiting.run_id)
    assert record["status"] == "cancelled", record["status"]
    assert "cancelled" in str(record.get("error"))


@pytest.mark.asyncio
async def test_a_budget_parked_run_that_is_cancelled_releases_its_request_counter(tmp_path):
    from test_budget_enforcement import _agent as _budget_agent
    from test_budget_pause import ONE_CALL, _two_turns

    agent = await _budget_agent(_two_turns(), budgets=ONE_CALL)
    manager = _manager(tmp_path)
    await manager.register_agent("payer", agent)
    await manager.register_task(task_id="spend", agent_id="payer", query="go", schedule={"type": "manual"})
    waiting = await manager.run_now("spend", wait=True, timeout_seconds=15)
    assert waiting.status == RunStatus.AWAITING_BUDGET
    ledger_key = f"request:{waiting.run_id}:total"
    assert await agent.memory_router.get_budget_state(ledger_key) is not None

    await manager.cancel_run(waiting.run_id)

    assert (await agent.get_run(waiting.run_id))["status"] == "cancelled"
    assert await agent.memory_router.get_budget_state(ledger_key) is None, "the request's counter is released"


@pytest.mark.asyncio
async def test_abandoning_a_run_the_agent_already_finished_changes_nothing(tmp_path):
    agent, manager = await _setup(tmp_path, "nothing to do")
    finished = await manager.run_now("tidy", wait=True, timeout_seconds=15)
    assert finished.status == RunStatus.COMPLETED

    await agent.abandon_run(finished.run_id, status="failed", reason="lease expired")

    assert (await agent.get_run(finished.run_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_a_run_left_running_by_a_dead_worker_is_closed_as_failed(tmp_path):
    from omnicoreagent.core.runs import update_from_outside

    agent, manager = await _setup(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    waiting = await manager.run_now("tidy", wait=True, timeout_seconds=15)
    # What a worker that died mid-step leaves behind.
    await update_from_outside(agent.memory_router, waiting.run_id, lambda r: r.__setitem__("status", "running"))

    await agent.abandon_run(waiting.run_id, status="failed", reason="lease expired")

    record = await agent.get_run(waiting.run_id)
    assert record["status"] == "failed" and "lease expired" in str(record.get("error"))
