#!/usr/bin/env python3
"""What one durable task-store write costs as the store fills up.

Scale plan, S2. The durable task stores were snapshot stores: each mutation
took a lock over the whole store, read all of its state, mutated it in memory
and wrote all of it back. The cost of one write therefore grew with everything
the store had ever kept, and nothing prunes run history. This measures it —
fill a store with finished runs, then time an ordinary run write — so the
claim that a write now touches one row is a number and not an assertion.

    python engineering/validation/task_store_cost.py                  # SQLite
    OMNICOREAGENT_TEST_POSTGRES_URL=postgresql://... python engineering/validation/task_store_cost.py
    MEASURE_REDIS=1 python engineering/validation/task_store_cost.py
    MEASURE_MONGODB=1 OMNICOREAGENT_TEST_MONGODB_URI=mongodb://... python ...

Numbers from a loaded machine mean nothing; run it on an idle one.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnicoreagent.background import (  # noqa: E402
    BackgroundAgentSpec,
    BackgroundRun,
    BackgroundTaskSpec,
    MongoDbTaskStore,
    OverlapPolicy,
    RedisTaskStore,
    RunStatus,
    SqlTaskStore,
)
from omnicoreagent.background.models import ScheduleType, TriggerType  # noqa: E402

SIZES = (100, 500, 2000)


def _run(task_id: str = "task") -> BackgroundRun:
    return BackgroundRun(
        task_id=task_id,
        agent_id="agent",
        query_snapshot=f"snapshot {task_id}",
        trigger_type=TriggerType.MANUAL,
        session_id=f"session-{uuid4().hex}",
        workspace_path=f"background/agent/{task_id}/run",
    )


async def _fill(store, count: int) -> None:
    """Finished runs: history, which a write should not have to read."""
    for _ in range(count):
        run = await store.create_run_with_overlap_guard(
            _run(), overlap_policy=OverlapPolicy.ALLOW_PARALLEL
        )
        await store.transition_run(run.run_id, {RunStatus.QUEUED}, RunStatus.CANCELLED)


async def _one_write(store, samples: int = 10) -> float:
    """Milliseconds for one ordinary write, at whatever size the store is."""
    timings = []
    for _ in range(samples):
        started = time.perf_counter()
        await store.create_run_with_overlap_guard(
            _run(), overlap_policy=OverlapPolicy.ALLOW_PARALLEL
        )
        timings.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(timings), 2)


async def _measure(make_store, name: str) -> None:
    store = make_store()
    await store.initialize()
    try:
        await store.save_agent(BackgroundAgentSpec(agent_id="agent"))
        await store.save_task(
            BackgroundTaskSpec(
                task_id="task",
                agent_id="agent",
                query="q",
                schedule={"type": ScheduleType.INTERVAL, "seconds": 3600},
                overlap_policy=OverlapPolicy.ALLOW_PARALLEL,
            )
        )
        held = 0
        for size in SIZES:
            await _fill(store, size - held)
            held = size
            print(
                f"{name:9} runs held {size:6}  one write {await _one_write(store):8} ms",
                flush=True,
            )
    finally:
        await store.close()


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        await _measure(
            lambda: SqlTaskStore(f"sqlite:///{Path(directory) / 'background.db'}"),
            "sqlite",
        )
    postgres = os.environ.get("OMNICOREAGENT_TEST_POSTGRES_URL")
    if postgres:
        await _measure(
            lambda: SqlTaskStore(postgres, table_prefix=f"cost{uuid4().hex[:8]}_"),
            "postgres",
        )
    if os.environ.get("MEASURE_REDIS"):
        url = os.environ.get("OMNICOREAGENT_TEST_REDIS_URL", "redis://localhost:6379/0")
        await _measure(
            lambda: RedisTaskStore(url, prefix=f"cost:{uuid4().hex}"), "redis"
        )
    if os.environ.get("MEASURE_MONGODB"):
        uri = os.environ.get("OMNICOREAGENT_TEST_MONGODB_URI", "mongodb://localhost:27017")
        await _measure(
            lambda: MongoDbTaskStore(
                uri=uri,
                database=os.environ.get(
                    "OMNICOREAGENT_TEST_MONGODB_DATABASE", "omnicoreagent_test"
                ),
                collection_prefix=f"cost_{uuid4().hex}",
                connect_timeout=10,
            ),
            "mongodb",
        )


if __name__ == "__main__":
    asyncio.run(main())
