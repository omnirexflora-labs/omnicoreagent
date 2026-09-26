"""A run's deadline holds even when the run does not stop when told
(stranger test S5, 2026-09-26).

A nightly reporter gave its background task `timeout_seconds=60`; the run
ended at 188 s. `run_with_timeout` cancelled the run, then waited with no
limit for it to finish stopping, so work that ignores cancellation (a
blocking call in a thread, a provider call that swallows it) held the run
past its deadline.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnicoreagent.core.runtime import deadline


@pytest.mark.asyncio
async def test_the_deadline_is_kept_when_the_run_ignores_cancellation(monkeypatch):
    monkeypatch.setattr(deadline, "CANCEL_GRACE_SECONDS", 0.2)

    async def stubborn():
        # Work that does not stop when told, and finishes on its own later:
        # the provider call that took 188 s.
        finish = time.monotonic() + 1.5
        while time.monotonic() < finish:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await deadline.run_with_timeout(stubborn(), 0.1)
    assert time.monotonic() - started < 1, "deadline + grace, not until the work gives up"
    await asyncio.sleep(1.6)  # let the abandoned work finish before the loop closes


@pytest.mark.asyncio
async def test_a_run_that_stops_when_told_is_waited_for():
    stopped = []

    async def polite():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            stopped.append(True)
            raise

    with pytest.raises(asyncio.TimeoutError):
        await deadline.run_with_timeout(polite(), 0.1)
    assert stopped == [True], "it recorded its own stop before the deadline returned"
