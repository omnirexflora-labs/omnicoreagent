"""Milliseconds of runtime per step, in process, against the fake provider.

    uv run python apps/support_desk/load/stepbench.py [--runs 64] [--concurrency 1 32]

Each run is the refund scenario (an order lookup, a refund that waits for a
person, the approval, the resume): three model calls, so three steps. The
fake provider answers with no delay, so what is timed is the runtime and its
database, not the model. Reported per concurrency:

- ``wall ms/step``: elapsed time for all the runs, divided by their steps.
  This is the cost of a step when the process is kept busy, and it is what
  the event loop and the database threads can sustain.
- ``cpu ms/step``: process CPU time per step, the part that no waiting hides.
- ``latency p50/p95``: one run's elapsed time divided by its steps, while the
  others run beside it.

The fake provider runs in this process, so its own work is in every number;
it is the same before and after a change, which is what the comparison needs.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE.parent, HERE.parent.parent.parent / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import roundtrips  # noqa: E402
from test_fake_provider import running_fake_provider  # noqa: E402

STEPS_PER_RUN = 3


async def _one(agent, index: int) -> float:
    started = time.perf_counter()
    await roundtrips.refund_scenario(agent, f"bench-{index}")
    return (time.perf_counter() - started) * 1000 / STEPS_PER_RUN


async def bench(url: str, runs: int, levels: list[int]) -> list[dict]:
    results = []
    with roundtrips.desk_environment(url):
        agent = roundtrips.load_desk().create_agent()
        await agent.initialize()
        await roundtrips.refund_scenario(agent, "warm")
        counter = roundtrips.RoundTripCounter()
        counter.attach(agent.memory_router.memory_store)
        index = 0
        for level in levels:
            counter.reset()
            cpu_before, wall_before = time.process_time(), time.perf_counter()
            latencies: list[float] = []
            gate = asyncio.Semaphore(level)

            async def worker(i: int) -> None:
                async with gate:
                    latencies.append(await _one(agent, i))

            await asyncio.gather(*(worker(index + i) for i in range(runs)))
            index += runs
            wall = (time.perf_counter() - wall_before) * 1000
            cpu = (time.process_time() - cpu_before) * 1000
            steps = runs * STEPS_PER_RUN
            latencies.sort()
            results.append(
                {
                    "concurrency": level,
                    "wall_ms_per_step": wall / steps,
                    "cpu_ms_per_step": cpu / steps,
                    "latency_p50": statistics.median(latencies),
                    "latency_p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
                    "transactions_per_run": counter.snapshot()["total_transactions"] / runs,
                }
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=64)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 32])
    args = parser.parse_args()
    with running_fake_provider() as url:
        rows = asyncio.run(bench(url, args.runs, args.concurrency))
    print(f"{'conc':>4} {'wall ms/step':>13} {'cpu ms/step':>12} {'p50':>8} {'p95':>8} {'txn/run':>8}")
    for r in rows:
        print(
            f"{r['concurrency']:>4} {r['wall_ms_per_step']:>13.1f} {r['cpu_ms_per_step']:>12.1f} "
            f"{r['latency_p50']:>8.1f} {r['latency_p95']:>8.1f} {r['transactions_per_run']:>8.1f}"
        )


if __name__ == "__main__":
    main()
