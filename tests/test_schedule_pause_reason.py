"""A schedule that governance stops says why.

Found by the repository steward's P7 week: its policy gained one rule, and at
their next due time both scheduled tasks were paused — the tasks were bound
to the earlier policy's snapshot, which is right to refuse — with no run, no
event, no log line and no reason anywhere. Hours of scheduled work went
missing silently. The pause is kept; it now records why, and says so in the
log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from omnicoreagent.background import BackgroundAgentManager
from omnicoreagent.background.store.in_memory import InMemoryTaskStore
from omnicoreagent.governance import GovernanceEngine
from test_background_agent import FakeAgent, _background_governance_policy


@pytest.mark.asyncio
async def test_a_schedule_paused_by_a_policy_change_says_why(caplog):
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(
        task_store=store,
        governance_engine=GovernanceEngine(_background_governance_policy(name="first-policy")),
    )
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="nightly",
        agent_id="agent",
        query="scheduled work",
        schedule={"type": "once", "run_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
    )
    manager.governance_engine = GovernanceEngine(_background_governance_policy(name="changed-policy"))

    with caplog.at_level(logging.WARNING), pytest.raises(Exception):
        await manager._dispatch_due_schedules()

    state = await store.get_schedule_state("nightly")
    assert state.paused is True
    assert state.paused_reason and "policy snapshot" in state.paused_reason, state
    status = await manager.get_task_status("nightly")
    assert "policy snapshot" in str(status["schedule_state"]["paused_reason"])
    assert "nightly" in caplog.text and "policy snapshot" in caplog.text


@pytest.mark.asyncio
async def test_resuming_a_schedule_clears_the_reason():
    store = InMemoryTaskStore()
    manager = BackgroundAgentManager(task_store=store)
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(
        task_id="hourly", agent_id="agent", query="work", schedule={"type": "interval", "seconds": 3600}
    )
    await store.set_schedule_paused("hourly", True, reason="because")
    assert (await store.get_schedule_state("hourly")).paused_reason == "because"

    await manager.resume_task("hourly")

    state = await store.get_schedule_state("hourly")
    assert state.paused is False and state.paused_reason is None


@pytest.mark.asyncio
async def test_registering_a_paused_task_again_binds_it_to_the_current_policy():
    # The 0.5.0rc5 gate: after one budget number changed, every scheduled task
    # paused; registering it again with replace=True (what the docs say
    # overwrites a task) was refused, 403 expired_policy, and the only way
    # out was to delete it. The reason now says how to go on.
    store = InMemoryTaskStore()
    first = GovernanceEngine(_background_governance_policy(name="first-policy"))
    manager = BackgroundAgentManager(task_store=store, governance_engine=first)
    await manager.register_agent("agent", FakeAgent(response="complete"))
    spec = dict(task_id="nightly", agent_id="agent", query="scheduled work",
                schedule={"type": "once", "run_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    await manager.register_task(**spec)
    manager.governance_engine = GovernanceEngine(_background_governance_policy(name="changed-policy"))
    with pytest.raises(Exception):
        await manager._dispatch_due_schedules()
    assert "replace" in (await store.get_schedule_state("nightly")).paused_reason

    task = await manager.register_task(**spec, replace=True)

    from omnicoreagent.governance.snapshots import POLICY_SNAPSHOT_METADATA_KEY, policy_snapshot_from_engine

    current = policy_snapshot_from_engine(manager.governance_engine)
    assert task.metadata[POLICY_SNAPSHOT_METADATA_KEY]["policy_hash"] == current["policy_hash"]


@pytest.mark.asyncio
async def test_a_snapshot_refusal_names_its_capability():
    # The 0.5.0rc5 gate: OmniServe's 403 for this refusal said capability null.
    from omnicoreagent.governance.errors import PolicyDeniedError

    manager = BackgroundAgentManager(
        task_store=InMemoryTaskStore(),
        governance_engine=GovernanceEngine(_background_governance_policy(name="first-policy")),
    )
    await manager.register_agent("agent", FakeAgent(response="complete"))
    await manager.register_task(task_id="t", agent_id="agent", query="q", schedule={"type": "manual"})
    manager.governance_engine = GovernanceEngine(_background_governance_policy(name="changed-policy"))

    with pytest.raises(PolicyDeniedError) as refused:
        await manager.run_now("t")
    assert refused.value.metadata["capability"] == "background.run.start"
    with pytest.raises(PolicyDeniedError) as refused:
        await manager.resume_task("t")
    assert refused.value.metadata["capability"] == "background.task.resume"
