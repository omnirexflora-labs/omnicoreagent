"""A step boundary costs one store write, and still sees what others wrote.

Each step began with a read of the run's record (steering messages, a request
to stop) and then a save of the new step number. The save is compared against
the record's version, and a save that finds someone else wrote merges what
they wrote before it goes on, so the read told nothing the save did not. The
step boundary is now the save alone. Counted on the support desk (2026-10-07):
three reads of the record per refund run that did nothing.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.runs import RunTracker, update_from_outside


class _Counting:
    def __init__(self, store):
        self.store = store
        self.reads = 0
        self.saves = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def get_run_state(self, run_id):
        self.reads += 1
        return await self.store.get_run_state(run_id)

    async def save_run_state(self, record, expected_version):
        self.saves += 1
        return await self.store.save_run_state(record, expected_version)


async def _started():
    store = _Counting(InMemoryStore())
    run = RunTracker(store, run_id="run_1", session_id="s", agent_name="a")
    await run.start("trace_1")
    store.reads = store.saves = 0
    return store, run


@pytest.mark.asyncio
async def test_a_step_boundary_with_nothing_waiting_is_one_save_and_no_read():
    store, run = await _started()

    steered, stop = await run.begin_step(1)

    assert (steered, stop) == ([], False)
    assert (store.reads, store.saves) == (0, 1)
    assert (await store.get_run_state("run_1"))["step"] == 1


@pytest.mark.asyncio
async def test_a_message_steered_in_from_outside_is_delivered_once():
    store, run = await _started()
    await update_from_outside(
        store,
        "run_1",
        lambda record: record["inbox"].append(
            {"id": "m1", "content": "also Q3", "sender": "alice", "delivered": False}
        ),
    )

    steered, stop = await run.begin_step(1)
    again, _ = await run.begin_step(2)

    assert stop is False and [m["content"] for m in steered] == ["also Q3"]
    assert again == []
    stored = await store.get_run_state("run_1")
    assert stored["inbox"][0]["delivered"] is True and stored["inbox"][0]["content"] is None
    assert stored["step"] == 2


@pytest.mark.asyncio
async def test_a_request_to_stop_is_seen_and_the_step_does_not_advance():
    store, run = await _started()
    await run.begin_step(1)
    await update_from_outside(store, "run_1", lambda record: record.update(interrupt_requested=True))

    steered, stop = await run.begin_step(2)

    assert stop is True and steered == []
    assert run.record["step"] == 1, "a run stopped at the boundary has not begun step 2"


@pytest.mark.asyncio
async def test_a_run_taken_over_by_another_process_cannot_begin_a_step():
    from omnicoreagent.core.runs import RunStateConflict

    store, run = await _started()
    await update_from_outside(store, "run_1", lambda record: record.update(owner="owner_someone_else"))

    with pytest.raises(RunStateConflict):
        await run.begin_step(1)
