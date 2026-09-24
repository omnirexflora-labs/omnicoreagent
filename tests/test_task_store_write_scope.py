"""Scale plan S2: a durable write touches one run, not every run.

The durable task stores were snapshot stores: each mutation took a lock over
the whole store, read all of its state, and wrote all of it back. The cost of
one write therefore grew with everything the store had ever kept — measured
on SQLite, 56 ms with 100 runs held and 344 ms with 2000 — and nothing prunes
run history.

This holds the property that fixes it, without timing anything: fill a store
with finished runs, then count how many runs one ordinary write reads or
writes. A store that keeps a row per run touches the one it is writing; a
snapshot store touches all of them.
"""

from __future__ import annotations

import pytest

from omnicoreagent.background import (
    BackgroundAgentSpec,
    BackgroundRun,
    OverlapPolicy,
    RunStatus,
    SqlTaskStore,
)

from test_background_task_store_contract import background_run, task_spec


class RunTraffic:
    """How many runs crossed the serialization boundary."""

    def __init__(self, monkeypatch) -> None:
        self.count = 0
        for name in ("model_dump_json", "model_validate_json"):
            original = getattr(BackgroundRun, name)

            def counted(*args, _original=original, **kwargs):
                self.count += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(BackgroundRun, name, counted)

    def reset(self) -> None:
        self.count = 0


async def _fill(store, count: int) -> None:
    """Finished runs, so nothing a later write needs is among them."""
    for _ in range(count):
        run = await store.create_run_with_overlap_guard(
            background_run(), overlap_policy=OverlapPolicy.ALLOW_PARALLEL
        )
        await store.transition_run(run.run_id, {RunStatus.QUEUED}, RunStatus.CANCELLED)


@pytest.mark.asyncio
async def test_a_write_does_not_read_every_run_ever_kept(tmp_path, monkeypatch):
    store = SqlTaskStore(url=f"sqlite:///{tmp_path / 'background.db'}")
    await store.initialize()
    try:
        await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
        await store.save_task(task_spec(overlap_policy=OverlapPolicy.ALLOW_PARALLEL))

        traffic = RunTraffic(monkeypatch)
        await _fill(store, 20)
        traffic.reset()
        await store.create_run_with_overlap_guard(
            background_run(), overlap_policy=OverlapPolicy.ALLOW_PARALLEL
        )
        small = traffic.count

        await _fill(store, 180)
        traffic.reset()
        await store.create_run_with_overlap_guard(
            background_run(), overlap_policy=OverlapPolicy.ALLOW_PARALLEL
        )
        large = traffic.count

        assert small <= 8, f"a write touched {small} runs with 21 kept"
        assert large == small, (
            f"a write touched {small} runs with 21 kept and {large} with 201: "
            "its cost grows with the store's history"
        )
    finally:
        await store.close()
