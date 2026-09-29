"""R16 (0.5.0rc1 gate): retention says what it removed, and keeps time in UTC.

Trace retention reported 0 removed when it had removed traces: the store's
first load in a process pruned first (trigger "load"), and the requested pass
then found nothing and reported that. And a workspace file's time was naive
local time that payload retention stamped as UTC, so on a UTC+1 host payloads
lived an hour past their window, and on UTC-N hosts went N hours early.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage


@pytest.fixture
def lagos_time():
    """A host whose local time is UTC+1, as the gate's."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Africa/Lagos"
    time.tzset()
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def test_a_workspace_files_time_is_utc(tmp_path, lagos_time):
    storage = LocalWorkspaceStorage(tmp_path)
    storage.write_text("note.txt", "x")

    (entry,) = storage.list_files()

    assert entry.modified_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - entry.modified_at).total_seconds()) < 60


@pytest.mark.asyncio
async def test_trace_retention_reports_what_it_removed(tmp_path):
    from omnicoreagent.core.telemetry.store import JsonlTelemetryStore
    from test_telemetry_retention import _trace

    path = tmp_path / "traces.jsonl"
    seed = JsonlTelemetryStore(path)
    await seed.upsert_trace(_trace("trace-old-1", ended_days_ago=30))
    await seed.upsert_trace(_trace("trace-old-2", ended_days_ago=30))
    await seed.flush()

    store = JsonlTelemetryStore(path, retention_days=7)
    removed = await store.prune()

    assert removed == 2
    assert store.retention_status()["last_prune"]["removed"] == 2
    assert await store.get_trace("trace-old-1") is None
