"""Milliseconds of runtime per step, in process, against the fake provider.

    uv run python apps/support_desk/load/stepbench.py [--runs 64] [--concurrency 1 32]
        [--database-url postgresql+psycopg2://...]

Each run is the refund scenario (an order lookup, a refund that waits for a
person, the approval, the resume): three model calls, so three steps. The
fake provider answers with no delay, so what is timed is the runtime and its
database, not the model. Reported per concurrency:

- ``wall ms/step``: elapsed time for all the runs, divided by their steps.
  This is the cost of a step when the process is kept busy, and it is what
  the event loop and the database threads can sustain.
- ``cpu ms/step``: process CPU time per step, the part that no waiting hides
  (every thread: the database threads, the client library, the fake provider).
- ``loop ms/step``: CPU time of the event loop's own thread per step. This is
  what a loaded server is short of: a step costs it this much however many
  database waits it overlaps. It does not depend on how busy the machine is.
- ``latency p50/p95``: one run's elapsed time divided by its steps, while the
  others run beside it.

``--history N`` gives every session N earlier exchanges (about 600 characters
each, with the personal details a support desk sees), as a customer's
conversation has after a while: the cost of a step that grows with the context
(token counts, redaction, digests) shows only then.

Without ``--database-url`` the memory store is a SQLite file, where a round
trip costs almost nothing; against Postgres each one is a network wait, which
is what the round-trip count and the database threads are about.

The fake provider runs in this process, so its own work is in every number;
it is the same before and after a change, which is what the comparison needs.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from uuid import uuid4
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE.parent, HERE.parent.parent.parent / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import roundtrips  # noqa: E402
from test_fake_provider import running_fake_provider  # noqa: E402

STEPS_PER_RUN = 3


EXCHANGE = (
    "Hi, I ordered a desk lamp last week and it has not arrived. My email is maya.chen@example.com "
    "and my phone is +1 (415) 555-0132. The order number is 1043, placed on 2026-09-18, total $129.99. "
    "Could you check the status and tell me when it ships? I also need the invoice for my records."
)
REPLY = (
    "Thanks Maya. Order 1043 is processing and ships within two business days; a tracking link will be "
    "emailed to you when the parcel leaves. I have noted your request for an invoice, which arrives with "
    "the confirmation. Is there anything else I can help you with today? Our returns policy lasts 30 days."
)


async def _prefill(agent, session_id: str, exchanges: int) -> None:
    for _ in range(exchanges):
        await agent.memory_router.store_message("user", EXCHANGE, {}, session_id)
        await agent.memory_router.store_message(
            "assistant", REPLY, {"agent_name": agent.name}, session_id
        )


async def _one(agent, index: int, tag: str, history: int = 0) -> float:
    if history:
        await _prefill(agent, f"bench-{tag}-{index}", history)
    started = time.perf_counter()
    await roundtrips.refund_scenario(agent, f"bench-{tag}-{index}")
    return (time.perf_counter() - started) * 1000 / STEPS_PER_RUN


async def bench(
    url: str, runs: int, levels: list[int], database_url: str | None, history: int = 0
) -> list[dict]:
    results = []
    tag = uuid4().hex[:6]
    with roundtrips.desk_environment(url, database_url=database_url):
        agent = roundtrips.load_desk().create_agent()
        await agent.initialize()
        await roundtrips.refund_scenario(agent, f"warm-{tag}")
        counter = roundtrips.RoundTripCounter()
        counter.attach(agent.memory_router.memory_store)
        index = 0
        for level in levels:
            counter.reset()
            cpu_before, wall_before = time.process_time(), time.perf_counter()
            loop_before = time.thread_time()
            latencies: list[float] = []
            gate = asyncio.Semaphore(level)

            async def worker(i: int) -> None:
                async with gate:
                    latencies.append(await _one(agent, i, tag, history))

            await asyncio.gather(*(worker(index + i) for i in range(runs)))
            index += runs
            wall = (time.perf_counter() - wall_before) * 1000
            cpu = (time.process_time() - cpu_before) * 1000
            loop = (time.thread_time() - loop_before) * 1000
            steps = runs * STEPS_PER_RUN
            latencies.sort()
            results.append(
                {
                    "concurrency": level,
                    "wall_ms_per_step": wall / steps,
                    "cpu_ms_per_step": cpu / steps,
                    "loop_ms_per_step": loop / steps,
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
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--history", type=int, default=0)
    args = parser.parse_args()
    with running_fake_provider() as url:
        rows = asyncio.run(bench(url, args.runs, args.concurrency, args.database_url, args.history))
    print(
        f"{'conc':>4} {'wall ms/step':>13} {'cpu ms/step':>12} {'loop ms/step':>13} "
        f"{'p50':>8} {'p95':>8} {'txn/run':>8}"
    )
    for r in rows:
        print(
            f"{r['concurrency']:>4} {r['wall_ms_per_step']:>13.1f} {r['cpu_ms_per_step']:>12.1f} "
            f"{r['loop_ms_per_step']:>13.1f} {r['latency_p50']:>8.1f} {r['latency_p95']:>8.1f} {r['transactions_per_run']:>8.1f}"
        )


if __name__ == "__main__":
    main()
