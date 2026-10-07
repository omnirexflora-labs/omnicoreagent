"""The server's sweep for runs whose process died.

A run keeps a heartbeat on its record while a process works on it. When the
process dies (a killed container, a lost database connection) the run stays
``running`` with a lapsed lease. Background runs have a supervisor that
recovers them; interactive runs, served over HTTP, had nobody: the support
desk chaos run (2026-10-07) left them waiting for someone to call
``resume(run_id)``. This sweep is that someone.

It only ever calls ``agent.claim_orphaned_runs``, which takes a run through
the record's own version check, so two replicas sweeping at once never resume
the same run, and which leaves a run whose decision is still pending alone. A
run whose person has decided, and which no client resumed within the grace
period, is taken too: the ramp at 100 users (2026-10-07) left 119 approved
runs waiting for a resume the admission limit had answered 503. A recovered
run then continues under the durable rules ``resume`` has always used.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from omnicoreagent.core.logging import logger
from omnicoreagent.core.runtime.deadline import run_with_timeout


class OrphanSweeper:
    """Resumes this agent's orphaned runs, on start and then periodically."""

    def __init__(
        self,
        agent: Any,
        *,
        interval_seconds: float = 30.0,
        max_concurrent: int = 2,
        max_recoveries: int = 3,
        decided_grace_seconds: float = 30.0,
        run_timeout: float | None = None,
    ) -> None:
        self.agent = agent
        self.interval_seconds = interval_seconds
        self.max_concurrent = max_concurrent
        self.max_recoveries = max_recoveries
        self.decided_grace_seconds = decided_grace_seconds
        self.run_timeout = run_timeout
        self.sweeps = 0
        self.recovered = 0
        self._loop_task: asyncio.Task | None = None
        self._resuming: set[asyncio.Task] = set()
        # Claims whose resume has not finished: a claim's lease is kept alive
        # until its run takes over, so one nobody resumes must be let go.
        self._claims: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name="omniserve-orphan-sweep")

    async def stop(self) -> None:
        """Stop sweeping and cancel the runs it was resuming. A cancelled run
        records itself as such; a killed one is found by the next server."""
        tasks = [task for task in (self._loop_task, *self._resuming) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None
        self._resuming.clear()
        for claim in list(self._claims.values()):
            await self._release(claim)
        self._claims.clear()

    async def _release(self, claim: dict[str, Any]) -> None:
        release = getattr(self.agent, "release_claim", None)
        if release is not None:
            try:
                await release(claim)
            except Exception as exc:  # noqa: BLE001 - nothing more to do for it.
                logger.warning(f"OmniServe: could not release claim {claim['run_id']}: {exc}")

    async def sweep_once(self) -> int:
        """Claim orphaned runs up to the free slots and resume each in its own
        task. Returns how many were claimed."""
        free = self.max_concurrent - len(self._resuming)
        if free <= 0:
            return 0
        claims = await self.agent.claim_orphaned_runs(
            limit=free,
            max_recoveries=self.max_recoveries,
            decided_grace_seconds=self.decided_grace_seconds,
        )
        self.sweeps += 1
        for claim in claims:
            self._claims[claim["run_id"]] = claim
            task = asyncio.create_task(
                self._resume(claim), name=f"omniserve-recover-{claim['run_id']}"
            )
            self._resuming.add(task)
            task.add_done_callback(self._resuming.discard)
        self.recovered += len(claims)
        return len(claims)

    async def _resume(self, claim: dict[str, Any]) -> None:
        run_id = claim["run_id"]
        try:
            result = await run_with_timeout(
                self.agent.resume_claimed(claim), self.run_timeout
            )
            logger.info(
                f"OmniServe: recovered run {run_id} ended {result.get('status')}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The run's record already says how it failed.
            logger.warning(
                f"OmniServe: recovered run {run_id} failed ({type(exc).__name__}: {exc})"
            )
        finally:
            self._claims.pop(run_id, None)
            await self._release(claim)

    async def _loop(self) -> None:
        # Replicas started together must not sweep together: the first sweep
        # waits a random part of the interval (at most five seconds, so a
        # restarted server finds its dead predecessor's runs soon), and every
        # later wait varies by a quarter either way.
        await asyncio.sleep(random.uniform(0, min(5.0, self.interval_seconds)))
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"OmniServe: orphan sweep failed ({type(exc).__name__}: {exc})"
                )
            await asyncio.sleep(self.interval_seconds * random.uniform(0.75, 1.25))
