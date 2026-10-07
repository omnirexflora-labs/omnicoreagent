"""How many agent runs one OmniServe process takes at once.

The support desk ramp (2026-10-07): on a 2-core container throughput topped
out at 3-4.5 requests a second, while chat p50 grew from 4 s at 10 users to
37 s at 100. Every request was accepted, so all of them slowed down together
and the callers that gave up still held their runs. A process now admits as
many concurrent runs as the CPUs it can actually use can serve. A request over
the limit waits a short, bounded time for a slot, then is told to come back
(503 with ``Retry-After``) before any work is done for it.

Only routes that start or resume a run take a slot. Health, readiness,
metrics, reads and approvals never wait here.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections import deque
from pathlib import Path
from typing import AsyncIterator

from fastapi import Request

# The starting multiple of CPUs. A run spends most of its time waiting on the
# model, so one CPU serves many; the final number is set from the knee
# measured after this change, not guessed.
DEFAULT_RUNS_PER_CPU = 16

_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")


class ServerBusyError(Exception):
    """No run slot freed within the wait; the caller should retry."""

    def __init__(self, *, limit: int, waited: float, retry_after: int):
        super().__init__(
            f"This server is running its maximum of {limit} concurrent runs."
        )
        self.limit = limit
        self.waited = waited
        self.retry_after = retry_after


def usable_cpus(
    *, cpu_max_path: Path | str = _CGROUP_V2_CPU_MAX, affinity: int | None = None
) -> int:
    """The CPUs this process can really use.

    ``os.cpu_count()`` reports the host, which is wrong in a container capped
    at two cores (the cgroup quota) or pinned to some of them (affinity). The
    smaller of the quota and the affinity wins.
    """
    if affinity is None:
        try:
            affinity = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            affinity = os.cpu_count() or 1
    cpus = max(1, affinity)
    try:
        quota, period = Path(cpu_max_path).read_text().split()[:2]
        if quota != "max":
            cpus = min(cpus, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return cpus


def default_max_concurrent_runs(**kwargs) -> int:
    return DEFAULT_RUNS_PER_CPU * usable_cpus(**kwargs)


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
