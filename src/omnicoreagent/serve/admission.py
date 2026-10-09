"""How many agent runs one OmniServe process takes at once.

The support desk ramp (2026-10-07): on a 2-core container throughput topped
out at 3-4.5 requests a second, while chat p50 grew from 4 s at 10 users to
37 s at 100. Every request was accepted, so all of them slowed down together
and the callers that gave up still held their runs. A process now admits as
many concurrent runs as one event loop can serve well: 24, the knee measured
on a server. A request over the limit waits a short, bounded time for a slot,
then is told to come back (503 with ``Retry-After``) before any work is done
for it.

Only routes that start or resume a run take a slot. Health, readiness,
metrics, reads and approvals never wait here.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from typing import AsyncIterator

from fastapi import Request

# The knee measured on a server (2026-10-07, support desk, fake model at 0.8
# to 3 s a call, Postgres, one process on a 2-CPU cap, limit lifted). Throughput
# was 5.1/s at 24 users, 5.5/s at 32 and 5.4/s at 48, then fell to 5.0/s at 64
# while p95 climbed from 8.9 s to 22.8 s. At 24 the process gave about 93% of
# its peak with p95 within 16% of idle. The process used about 1.1 cores: one
# event loop cannot use a second core, so the number is per process and does
# not grow with the CPU count. More CPUs means more processes behind a load
# balancer (two 1-CPU replicas peaked at 9.4/s, 1.7x one process).
DEFAULT_MAX_CONCURRENT_RUNS = 24


class ServerBusyError(Exception):
    """No run slot freed within the wait; the caller should retry."""

    def __init__(self, *, limit: int, waited: float, retry_after: int):
        super().__init__(
            f"This server is running its maximum of {limit} concurrent runs."
        )
        self.limit = limit
        self.waited = waited
        self.retry_after = retry_after


def default_max_concurrent_runs() -> int:
    return DEFAULT_MAX_CONCURRENT_RUNS


class RunSlot:
    """One admitted run. Releasing twice is harmless."""

    def __init__(self, limiter: "RunAdmission | None"):
        self._limiter = limiter

    def release(self) -> None:
        limiter, self._limiter = self._limiter, None
        if limiter is not None:
            limiter._release()

    async def around(self, stream: AsyncIterator) -> AsyncIterator:
        """Hold the slot for as long as a stream is being sent."""
        try:
            async for item in stream:
                yield item
        finally:
            self.release()


class RunAdmission:
    """A per-process cap on concurrent runs, with a bounded wait for a slot.

    ``limit`` of 0 means unlimited; the counters still run, so the metrics
    show the load either way. Created without an event loop: it only holds
    futures made by the loop that waits on them.
    """

    def __init__(self, limit: int, wait_seconds: float):
        self.limit = limit
        self.wait_seconds = wait_seconds
        self.in_flight = 0
        self.rejected = 0
        self._waiters: deque[asyncio.Future] = deque()

    async def acquire(self) -> RunSlot:
        if self.limit <= 0 or (self.in_flight < self.limit and not self._waiters):
            self.in_flight += 1
            return RunSlot(self)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self._waiters.append(waiter)
        started = loop.time()
        try:
            await asyncio.wait_for(waiter, self.wait_seconds)
        except asyncio.TimeoutError:
            self._forget(waiter)
            self.rejected += 1
            raise ServerBusyError(
                limit=self.limit,
                waited=loop.time() - started,
                retry_after=max(1, math.ceil(self.wait_seconds)),
            ) from None
        except BaseException:
            # Cancelled (the caller left). If a slot was handed over in the
            # same instant, give it back so it is not lost.
            self._forget(waiter)
            if waiter.done() and not waiter.cancelled():
                self._release()
            raise
        return RunSlot(self)

    def _forget(self, waiter: asyncio.Future) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass

    def _release(self) -> None:
        self.in_flight -= 1
        # Hand the freed slot straight to the longest waiter, so a new
        # arrival cannot overtake it.
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                self.in_flight += 1
                waiter.set_result(True)
                return

    def prometheus_lines(self) -> list[str]:
        return [
            "# TYPE omniserve_runs_in_flight gauge",
            f"omniserve_runs_in_flight {self.in_flight}",
            "# TYPE omniserve_runs_waiting gauge",
            f"omniserve_runs_waiting {len(self._waiters)}",
            "# TYPE omniserve_runs_limit gauge",
            f"omniserve_runs_limit {max(self.limit, 0)}",
            "# TYPE omniserve_runs_rejected_total counter",
            f"omniserve_runs_rejected_total {self.rejected}",
        ]


async def admit(request: Request) -> RunSlot:
    """Take a run slot for this request, or raise ``ServerBusyError``."""
    return await request.app.state.run_admission.acquire()


async def run_slot(request: Request) -> AsyncIterator[RunSlot]:
    """A dependency for routes that run to completion before they answer.

    The slot is held until the route returns or fails. Streaming routes take
    theirs with ``admit`` and hold it for the whole stream instead.
    """
    slot = await admit(request)
    try:
        yield slot
    finally:
        slot.release()
