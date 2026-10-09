"""Closing a resumed run's dead segments costs the run, not the store.

The support desk ramp (2026-10-07): `_close_dead_segments` runs on every
resume and listed every trace of the run through `list_traces`, which in the
stores walks and copies all traces before it filters. A profile at 30 users
put about 5% of samples there, growing with the telemetry store. The run's
record already names its segments (`trace_ids`), so the resume reads those
and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import TraceStatus
from omnicoreagent.core.telemetry.models import TelemetryTrace
from omnicoreagent.core.telemetry.store import InMemoryTelemetryStore


class CountingStore(InMemoryTelemetryStore):
    def __init__(self):
        super().__init__()
        self.listed = 0
        self.fetched: list[str] = []

    async def list_traces(self, filter=None):
        self.listed += 1
        return await super().list_traces(filter)

    async def get_trace(self, trace_id):
        self.fetched.append(trace_id)
        return await super().get_trace(trace_id)


def _trace(trace_id, run_id, status=TraceStatus.RUNNING, ended=False):
    now = datetime.now(timezone.utc)
    return TelemetryTrace(
        trace_id=trace_id,
        root_span_id=f"span-{trace_id}",
        run_id=run_id,
        status=status,
        ended_at=now if ended else None,
    )


async def _store_with_noise(n=5000):
    store = CountingStore()
    for i in range(n):
        await store.upsert_trace(_trace(f"noise-{i}", f"other-{i % 50}"))
    await store.upsert_trace(_trace("seg-dead", "run-1"))
    await store.upsert_trace(_trace("seg-done", "run-1", TraceStatus.COMPLETED, ended=True))
    await store.upsert_trace(_trace("seg-now", "run-1"))
    return store


@pytest.mark.asyncio
async def test_only_the_runs_own_segments_are_read_and_the_dead_one_is_closed():
    store = await _store_with_noise()
    agent = SimpleNamespace(telemetry_store=store)

    await OmniCoreAgent._close_dead_segments(
        agent, "run-1", "seg-now", ["seg-dead", "seg-done"]
    )

    assert store.listed == 0, "a resume must not list the telemetry store"
    assert sorted(store.fetched) == ["seg-dead", "seg-done"]
    dead = await store.get_trace("seg-dead")
    assert dead.status == TraceStatus.INTERRUPTED
    assert dead.ended_at is not None
    assert (await store.get_trace("seg-done")).status == TraceStatus.COMPLETED
    # The segment now running is the resume's own and is left alone, as are
    # unrelated traces that were still running.
    assert (await store.get_trace("seg-now")).status == TraceStatus.RUNNING
    assert (await store.get_trace("noise-7")).status == TraceStatus.RUNNING


@pytest.mark.asyncio
async def test_a_missing_trace_does_not_stop_the_others():
    store = await _store_with_noise(10)
    agent = SimpleNamespace(telemetry_store=store)

    await OmniCoreAgent._close_dead_segments(
        agent, "run-1", "seg-now", ["gone", "seg-dead"]
    )

    assert (await store.get_trace("seg-dead")).status == TraceStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_a_record_without_trace_ids_falls_back_to_the_runs_listing():
    # Records written before they named their segments.
    store = await _store_with_noise(10)
    agent = SimpleNamespace(telemetry_store=store)

    await OmniCoreAgent._close_dead_segments(agent, "run-1", "seg-now", [])

    assert store.listed == 1
    assert (await store.get_trace("seg-dead")).status == TraceStatus.INTERRUPTED
