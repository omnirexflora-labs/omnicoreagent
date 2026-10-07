"""A person's decision is not lost to the run's last heartbeat.

Found by the step benchmark against Postgres (2026-10-07): 32 refund runs at
once, and one ``resolve_approval`` raised ``RunStateConflict: ... changed since
version 11``. A run that has paused for approval stops its keep-alive task as
it returns, but a heartbeat save already sent to the database still lands
after it, bumping the record's version between the moment ``decide`` reads the
record and the moment it saves it. ``decide`` read and saved once, so the
person's yes failed with a conflict about a record nothing of theirs had
touched. It now reads again and re-checks the approval, as the other writers
from outside the run do (``update_from_outside``).
"""

from __future__ import annotations

import copy

import pytest

from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.run_approvals import decide
from omnicoreagent.core.runs import RunStateConflict


def _record():
    return {
        "run_id": "run_1",
        "session_id": "s",
        "status": "awaiting_approval",
        "step": 2,
        "approvals": [
            {
                "approval_id": "approval_1",
                "status": "pending",
                "tool_name": "issue_refund",
                "tool_provider": "local",
                "capability": "tool.local.call",
                "request_digest": "d",
                "expires_at": "2999-01-01T00:00:00+00:00",
            }
        ],
    }


class _HeartbeatLands:
    """A store whose record is saved by someone else once, right after a read."""

    def __init__(self, store, times=1):
        self.store = store
        self.times = times
        self.reads = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def get_run_state(self, run_id):
        record = await self.store.get_run_state(run_id)
        self.reads += 1
        if self.times:
            self.times -= 1
            # The heartbeat: the same record, one version later.
            other = dict(record)
            version = other.pop("version")
            await self.store.save_run_state({**other, "heartbeat_at": "later"}, expected_version=version)
        return record


@pytest.mark.asyncio
async def test_a_heartbeat_between_the_read_and_the_save_does_not_fail_the_decision():
    inner = InMemoryStore()
    await inner.save_run_state(_record(), expected_version=None)
    store = _HeartbeatLands(inner)

    approval = await decide(store, "run_1", "approval_1", decision="approve", approver="dana")

    assert approval["status"] == "approved" and approval["approver"] == "dana"
    saved = await inner.get_run_state("run_1")
    assert saved["approvals"][0]["status"] == "approved"
    assert saved["heartbeat_at"] == "later", "the heartbeat's write is kept"
    assert store.reads == 2


@pytest.mark.asyncio
async def test_a_decision_made_by_someone_else_meanwhile_is_not_overwritten():
    inner = InMemoryStore()
    await inner.save_run_state(_record(), expected_version=None)

    class _Raced(_HeartbeatLands):
        async def get_run_state(self, run_id):
            record = await self.store.get_run_state(run_id)
            if self.times:
                self.times -= 1
                other = copy.deepcopy(record)
                version = other.pop("version")
                other["approvals"][0].update(status="denied", approver="erin")
                await self.store.save_run_state(other, expected_version=version)
            return record

    with pytest.raises(ValueError, match="already denied"):
        await decide(_Raced(inner), "run_1", "approval_1", decision="approve", approver="dana")
    assert (await inner.get_run_state("run_1"))["approvals"][0]["approver"] == "erin"


@pytest.mark.asyncio
async def test_a_record_that_keeps_changing_still_ends_in_a_conflict():
    inner = InMemoryStore()
    await inner.save_run_state(_record(), expected_version=None)
    with pytest.raises(RunStateConflict):
        await decide(_HeartbeatLands(inner, times=100), "run_1", "approval_1", decision="approve", approver="dana")
