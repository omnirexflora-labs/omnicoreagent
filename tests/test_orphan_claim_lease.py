"""A claimed run's lease does not lapse while its resume is still starting.

The orphan sweep claims a run (new owner, fresh heartbeat) and hands it to
``resume_claimed``. Before the run's own heartbeat starts it initializes the
agent and loads the model client, which took about 74 s on a cold process
against a default lease of 60 s. A second sweeper then found the lease lapsed
and claimed the run again. The loser's start save failed on the version check,
but ``resume`` had already removed the dead attempt's sandboxes, and the
loser could remove the winner's sandbox. (Found merging the P6 tracks,
2026-10-07.)

The rules these tests hold: the claimer keeps the lease alive until the run's
own heartbeat takes over, and sandboxes of a dead attempt are removed only
after this process has won the save that makes the run its own.
"""

from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.core.runs import RunStateConflict, lease_expired

from test_orphan_sweep import _orphan, _survivor
from test_run_recovery import _tools
from test_run_suspend import RecordingModel


class FakeSandboxRuntime:
    """Records which runs' sandboxes it was asked to remove."""

    def __init__(self):
        self.removed: list[str] = []

    async def cleanup_orphans(self, *, run_id=None):
        self.removed.append(run_id)
        return 1


class SlowModel(RecordingModel):
    """A model client whose first load lasts until the test lets it finish,
    so it is always longer than the lease, however loaded the machine is."""

    def __init__(self, *turns):
        super().__init__(*turns)
        self.loaded = asyncio.Event()

    async def warm_up(self):
        await self.loaded.wait()


async def _lease_lapsed(router, run_id="run_orphan", *, within=30.0):
    """Wait until the run's lease has really lapsed, as the claim sees it.

    These tests used fixed sleeps of 1.3 to 1.8 s against a 1 s lease. Under
    a loaded machine (the full suite) a heartbeat that was in flight when its
    owner stopped landed late, the lease outlived the sleep, and the second
    claimer found the run still held. Waiting on the record's own heartbeat
    and lease is the condition the claim checks, and costs the lease and no
    more.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while loop.time() < deadline:
        record = await router.get_run_state(run_id)
        if record is not None and lease_expired(record):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"the lease of {run_id} did not lapse within {within} s")


def _with_sandbox(agent):
    runtime = FakeSandboxRuntime()
    agent.agent.governance_engine.sandbox_runtime = runtime
    return runtime


@pytest.mark.asyncio
async def test_a_slow_start_never_lets_the_lease_lapse(tmp_path):
    dead = await _orphan(tmp_path)
    router = dead.memory_router
    # Lease 1 s; the model client loads until the test says so.
    slow = SlowModel("recovered")
    winner = await _survivor(router, slow, _tools(tmp_path / "l1"))
    rival = await _survivor(router, RecordingModel("rival"), _tools(tmp_path / "l2"))
    await _lease_lapsed(router)

    claims = await winner.claim_orphaned_runs()
    assert len(claims) == 1
    resuming = asyncio.create_task(winner.resume_claimed(claims[0]))
    # Past the lease, while the winner is still loading its model client:
    # the time since the claim is measured, not assumed from a sleep.
    loop = asyncio.get_running_loop()
    claimed_at = loop.time()
    while loop.time() - claimed_at < 1.8:
        await asyncio.sleep(0.05)
    second_sweep = await rival.claim_orphaned_runs()
    slow.loaded.set()
    result = await resuming

    assert second_sweep == [], "a second sweeper claimed a run whose start was only slow"
    assert result["status"] == "success"
    assert (await winner.get_run("run_orphan"))["status"] == "completed"


@pytest.mark.asyncio
async def test_an_abandoned_claim_stops_its_heartbeat(tmp_path):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(dead.memory_router, RecordingModel("x"), _tools(tmp_path / "l1"))
    await _lease_lapsed(dead.memory_router)
    claims = await survivor.claim_orphaned_runs()

    await survivor.release_claim(claims[0])

    await _lease_lapsed(dead.memory_router)
    other = await _survivor(dead.memory_router, RecordingModel("y"), _tools(tmp_path / "l2"))
    assert [c["run_id"] for c in await other.claim_orphaned_runs()] == ["run_orphan"], (
        "a claim nobody resumed held the run's lease for good"
    )


@pytest.mark.asyncio
async def test_a_losing_claimer_never_touches_the_winners_sandbox(tmp_path):
    dead = await _orphan(tmp_path)
    router = dead.memory_router
    loser = await _survivor(router, RecordingModel("late"), _tools(tmp_path / "l1"))
    winner = await _survivor(router, RecordingModel("recovered"), _tools(tmp_path / "l2"))
    loser_sandboxes = _with_sandbox(loser)
    winner_sandboxes = _with_sandbox(winner)
    await _lease_lapsed(router)
    # The loser claimed first, then its lease lapsed (its heartbeat is stopped
    # here, as if the process stalled), and the winner claimed the run.
    stale = (await loser.claim_orphaned_runs())[0]
    await loser.release_claim(stale)
    await _lease_lapsed(router)
    winning = (await winner.claim_orphaned_runs())[0]
    assert winning["record"]["version"] > stale["record"]["version"]

    with pytest.raises(RunStateConflict):
        await loser.resume_claimed(stale)
    result = await winner.resume_claimed(winning)

    assert loser_sandboxes.removed == [], "the loser removed sandboxes of a run it never owned"
    assert winner_sandboxes.removed == ["run_orphan"]
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_the_dead_attempts_sandboxes_are_removed_after_the_run_is_ours(tmp_path):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(dead.memory_router, RecordingModel("recovered"), _tools(tmp_path / "l1"))
    sandboxes = _with_sandbox(survivor)
    saved_when_removed = []
    real_cleanup = sandboxes.cleanup_orphans

    async def cleanup(*, run_id=None):
        record = await survivor.memory_router.get_run_state(run_id)
        saved_when_removed.append(record["owner"])
        return await real_cleanup(run_id=run_id)

    sandboxes.cleanup_orphans = cleanup
    dead_owner = (await dead.get_run("run_orphan"))["owner"]
    await _lease_lapsed(dead.memory_router)

    await survivor.resume("run_orphan")

    # By the time the sandboxes went, the record already named this process.
    assert saved_when_removed and saved_when_removed[0] != dead_owner
