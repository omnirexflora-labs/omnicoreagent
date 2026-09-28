"""Pruned traces are counted, and stay pruned after a restart.

Found writing Telemetry and exporters (D8): finished traces live in the
archive, and pruning them (1) returned only what it removed from the log, so
prune() read 0 when it removed every trace, and (2) left their records in the
log, which is compacted only past a size threshold, so the next process
replayed the log and the pruned traces came back.
"""

from __future__ import annotations


import pytest

from omnicoreagent.core.telemetry import store as store_module
from test_telemetry_store_integrity import _event, _trace


def _fresh_store(path):
    # A new process: nothing cached from the one that wrote the log.
    store_module._SHARED_JSONL_STORES.clear()
    return store_module.shared_jsonl_telemetry_store(path)


@pytest.mark.asyncio
async def test_pruning_archived_traces_counts_them_and_they_stay_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", {})
    path = tmp_path / "traces.jsonl"
    writer = _fresh_store(path)
    for index in range(3):
        trace = _trace(f"trace-{index}", ended_days_ago=10)
        await writer.upsert_trace(trace)
        await writer.append_event(trace.trace_id, _event(trace.trace_id, 0))
    await writer.flush()
    assert len(await writer.list_traces()) == 3

    pruner = _fresh_store(path)
    removed = await pruner.prune(retention_days=7)
    assert removed == 3
    assert await pruner.list_traces() == []

    later = _fresh_store(path)
    assert await later.list_traces() == [], "pruned traces came back"


@pytest.mark.asyncio
async def test_a_kept_archived_trace_survives_the_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", {})
    path = tmp_path / "traces.jsonl"
    writer = _fresh_store(path)
    old, recent = _trace("trace-old", ended_days_ago=10), _trace("trace-recent", ended_days_ago=1)
    for trace in (old, recent):
        await writer.upsert_trace(trace)
        await writer.append_event(trace.trace_id, _event(trace.trace_id, 0))
    await writer.flush()

    await _fresh_store(path).prune(retention_days=7)

    later = _fresh_store(path)
    kept = await later.list_traces()
    assert [trace.trace_id for trace in kept] == ["trace-recent"]
    assert len((await later.get_trace("trace-recent")).events) == 1
