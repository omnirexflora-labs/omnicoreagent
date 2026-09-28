"""A trace whose process died is pruned once it has been silent too long.

Found writing Stores and scale (D7): retention removed only traces that had
ended, so a trace whose process died mid-run stayed `running` forever. The
maintainer's decision (2026-09-28): a running trace with no activity for
longer than the retention window is abandoned, and pruned; judged by its last
activity, so a long run that is still working is never touched. A trace
waiting for a person (suspended) is not abandoned.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from omnicoreagent.core.telemetry.models import TraceStatus, utc_now
from omnicoreagent.core.telemetry.store import JsonlTelemetryStore
from test_telemetry_store_integrity import _event, _trace


@pytest.mark.asyncio
async def test_a_long_silent_running_trace_is_pruned_as_abandoned(tmp_path):
    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")

    dead = _trace("trace-dead")
    dead.started_at = utc_now() - timedelta(days=30)
    dead.spans[0].started_at = dead.started_at
    await store.upsert_trace(dead)
    old = _event("trace-dead", 0)
    old.timestamp = utc_now() - timedelta(days=30)
    await store.append_event("trace-dead", old)

    long_but_alive = _trace("trace-alive")
    long_but_alive.started_at = utc_now() - timedelta(days=30)
    await store.upsert_trace(long_but_alive)
    await store.append_event("trace-alive", _event("trace-alive", 0))  # just now

    waiting = _trace("trace-waiting")
    waiting.started_at = utc_now() - timedelta(days=30)
    waiting.spans[0].started_at = waiting.started_at
    waiting.status = TraceStatus.SUSPENDED
    await store.upsert_trace(waiting)

    await store.prune(retention_days=7)

    assert await store.get_trace("trace-dead") is None
    assert await store.get_trace("trace-alive") is not None
    assert await store.get_trace("trace-waiting") is not None
    assert store.last_prune["abandoned"] == 1
