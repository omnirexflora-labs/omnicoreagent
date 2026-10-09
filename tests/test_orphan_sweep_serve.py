"""OmniServe resumes the runs a dead process left, and can be told not to.

The support desk chaos run (2026-10-07) left interactive runs `running` with
lapsed leases until a person called `resume`. The server sweeps for them on
start and then periodically; ``orphan_sweep_enabled=False`` turns it off.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniServe, OmniServeConfig
from omnicoreagent.serve.orphan_sweep import OrphanSweeper

from test_orphan_sweep import _orphan, _survivor
from test_run_recovery import _tools
from test_run_suspend import RecordingModel


def _wait_for(client, run_id, status, seconds=20):
    deadline = time.monotonic() + seconds
    record = None
    while time.monotonic() < deadline:
        record = client.get(f"/runs/{run_id}").json()
        if record.get("status") == status:
            return record
        time.sleep(0.1)
    return record


async def _server_for_an_orphan(tmp_path, **config):
    dead = await _orphan(tmp_path)
    survivor = await _survivor(
        dead.memory_router, RecordingModel("recovered"), _tools(tmp_path / "ledger")
    )
    return survivor, OmniServe(
        survivor, OmniServeConfig(background_enabled=False, orphan_sweep_interval_seconds=0.3, **config)
    )


@pytest.mark.asyncio
async def test_the_server_resumes_an_orphaned_run_by_itself(tmp_path):
    _, server = await _server_for_an_orphan(tmp_path)

    def serve():
        with TestClient(server.app) as client:
            return _wait_for(client, "run_orphan", "completed")

    record = await asyncio.to_thread(serve)

    assert record["status"] == "completed", record


@pytest.mark.asyncio
async def test_the_sweep_can_be_switched_off(tmp_path):
    _, server = await _server_for_an_orphan(tmp_path, orphan_sweep_enabled=False)

    def serve():
        with TestClient(server.app) as client:
            assert server.app.state.orphan_sweeper is None
            time.sleep(3)
            return client.get("/runs/run_orphan").json()

    record = await asyncio.to_thread(serve)

    assert record["status"] == "running", "nobody resumed it"


def test_the_sweep_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_SERVE_ORPHAN_SWEEP_ENABLED", "false")
    monkeypatch.setenv("OMNICOREAGENT_SERVE_ORPHAN_SWEEP_INTERVAL_SECONDS", "5")
    config = OmniServeConfig()
    assert config.orphan_sweep_enabled is False
    assert config.orphan_sweep_interval_seconds == 5.0
    assert OmniServeConfig.model_fields["orphan_sweep_enabled"].default is True
    assert OmniServeConfig.model_fields["orphan_sweep_interval_seconds"].default == 30.0


def test_a_nonsense_interval_is_refused():
    with pytest.raises(ValueError, match="INTERVAL_SECONDS"):
        OmniServeConfig(orphan_sweep_interval_seconds=0)


@pytest.mark.asyncio
async def test_a_sweep_resumes_no_more_runs_than_it_has_slots_for(tmp_path):
    class Agent:
        def __init__(self):
            self.limits = []

        async def claim_orphaned_runs(self, *, limit, max_recoveries, decided_grace_seconds):
            self.limits.append(limit)
            return [{"run_id": f"run_{i}"} for i in range(limit)]

        async def resume_claimed(self, claim):
            await asyncio.sleep(30)

    agent = Agent()
    sweeper = OrphanSweeper(agent, max_concurrent=2)

    assert await sweeper.sweep_once() == 2
    assert await sweeper.sweep_once() == 0, "both slots are busy"
    assert agent.limits == [2]
    await sweeper.stop()


@pytest.mark.asyncio
async def test_a_failing_sweep_does_not_end_the_loop():
    class Agent:
        calls = 0

        async def claim_orphaned_runs(self, *, limit, max_recoveries, decided_grace_seconds):
            Agent.calls += 1
            raise RuntimeError("the store is down")

    sweeper = OrphanSweeper(Agent(), interval_seconds=0.05)
    await sweeper.start()
    await asyncio.sleep(0.6)
    await sweeper.stop()

    assert Agent.calls >= 2


def test_the_grace_period_for_decided_runs_is_a_setting(monkeypatch):
    assert OmniServeConfig.model_fields["orphan_sweep_decided_grace_seconds"].default == 30.0
    monkeypatch.setenv("OMNICOREAGENT_SERVE_ORPHAN_SWEEP_DECIDED_GRACE_SECONDS", "7")
    assert OmniServeConfig().orphan_sweep_decided_grace_seconds == 7.0
    monkeypatch.delenv("OMNICOREAGENT_SERVE_ORPHAN_SWEEP_DECIDED_GRACE_SECONDS")
    with pytest.raises(ValueError, match="DECIDED_GRACE_SECONDS"):
        OmniServeConfig(orphan_sweep_decided_grace_seconds=-1)


@pytest.mark.asyncio
async def test_the_sweep_passes_its_grace_period_to_the_claim():
    class Agent:
        graces = []

        async def claim_orphaned_runs(self, *, limit, max_recoveries, decided_grace_seconds):
            Agent.graces.append(decided_grace_seconds)
            return []

    sweeper = OrphanSweeper(Agent(), decided_grace_seconds=12.5)
    await sweeper.sweep_once()

    assert Agent.graces == [12.5]


@pytest.mark.asyncio
async def test_a_backlog_is_swept_without_waiting_a_whole_interval_per_batch(monkeypatch):
    # The ramp left 119 approved runs to resume with two slots. A sweep that
    # waited its whole interval after every full batch would have taken an
    # hour; a full batch means more are probably waiting, so the next sweep
    # follows as soon as a slot frees.
    monkeypatch.setattr("omnicoreagent.serve.orphan_sweep.random.uniform", lambda low, high: low)

    class Agent:
        waiting = [f"run_{i}" for i in range(6)]
        resumed = []

        async def claim_orphaned_runs(self, *, limit, max_recoveries, decided_grace_seconds):
            batch, Agent.waiting = Agent.waiting[:limit], Agent.waiting[limit:]
            return [{"run_id": run_id} for run_id in batch]

        async def resume_claimed(self, claim):
            await asyncio.sleep(0.05)
            Agent.resumed.append(claim["run_id"])
            return {"status": "success"}

    sweeper = OrphanSweeper(Agent(), interval_seconds=60.0, max_concurrent=2)
    await sweeper.start()
    for _ in range(40):
        if len(Agent.resumed) == 6:
            break
        await asyncio.sleep(0.1)
    await sweeper.stop()

    assert len(Agent.resumed) == 6
