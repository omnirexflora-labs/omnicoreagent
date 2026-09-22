"""T5 of the telemetry storage plan: running traces in the log, finished ones archived.

The repository steward's OmniServe held every trace it had recorded in
memory: 303 MiB at rest became 942 MiB on its first trace read after a
restart, which took 6.3 seconds. The JSONL log keeps doing what it is good
at, durability while a trace is written. A finished trace moves to the
archive and leaves memory, the log is compacted to what is still running,
and the first start of an old log migrates it.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    JsonlTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
    TelemetryStreamScope,
    TraceFilter,
    TraceStatus,
)
from omnicoreagent.core.telemetry.archive import TelemetryArchive


def _store(tmp_path, *, archive: bool = True) -> JsonlTelemetryStore:
    return JsonlTelemetryStore(
        tmp_path / "traces.jsonl",
        archive=TelemetryArchive(tmp_path / "archive") if archive else None,
        compact_bytes=1,
    )


async def _record(store, number: int, *, end: bool = True, events: int = 3) -> str:
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id=f"trace_{number:032x}", run_id=f"run_{number}", session_id="s"
    )
    for index in range(events):
        await recorder.emit_event(
            "runtime_message", actor=TelemetryActor(type=ActorType.SYSTEM), metadata={"n": index}
        )
    if end:
        await recorder.end_trace(status=TraceStatus.COMPLETED)
    else:
        await store.flush()
    return context.trace_id


@pytest.mark.asyncio
async def test_a_finished_trace_leaves_memory_and_is_still_read_whole(tmp_path):
    store = _store(tmp_path)
    ids = [await _record(store, n) for n in range(1, 6)]

    assert store._inner._traces == {}, "finished traces are not held in memory"
    trace = await store.get_trace(ids[2])
    assert trace is not None and trace.status == TraceStatus.COMPLETED
    assert sum(e.event_type == "runtime_message" for e in trace.events) == 3
    (listed,) = await store.list_traces(TraceFilter(run_id="run_3"))
    assert listed.trace_id == ids[2]
    assert [t.trace_id for t in await store.list_traces()] == ids


@pytest.mark.asyncio
async def test_the_log_keeps_only_what_is_still_running(tmp_path):
    store = _store(tmp_path)
    for number in range(1, 4):
        await _record(store, number)
    running = await _record(store, 9, end=False)
    await store.flush()

    log = (tmp_path / "traces.jsonl").read_text()
    assert running in log
    assert f"trace_{1:032x}" not in log


@pytest.mark.asyncio
async def test_a_restart_reads_only_running_traces_and_keeps_counting(tmp_path):
    store = _store(tmp_path)
    finished = await _record(store, 1)
    running = await _record(store, 2, end=False)
    cursor = await store.get_stream_cursor(TelemetryStreamScope())
    await store.close()

    reopened = _store(tmp_path)
    assert (await reopened.get_trace(running)).status == TraceStatus.RUNNING
    assert set(reopened._inner._traces) == {running}
    assert (await reopened.get_trace(finished)).status == TraceStatus.COMPLETED
    assert int(await reopened.get_stream_cursor(TelemetryStreamScope())) >= int(cursor)


@pytest.mark.asyncio
async def test_an_old_log_is_migrated_on_its_first_start(tmp_path):
    old = _store(tmp_path, archive=False)
    ids = [await _record(old, n) for n in range(1, 4)]
    await old.close()
    before = (tmp_path / "traces.jsonl").stat().st_size

    store = _store(tmp_path)
    assert [t.trace_id for t in await store.list_traces()] == ids
    assert store._inner._traces == {}
    assert (tmp_path / "traces.jsonl").stat().st_size < before / 4


@pytest.mark.asyncio
async def test_a_stream_resumes_across_archived_and_running_traces(tmp_path):
    store = _store(tmp_path)
    await _record(store, 1)
    start = await store.get_stream_cursor(TelemetryStreamScope())
    await _record(store, 2)
    await _record(store, 3, end=False)

    events = await store.get_events_after(TelemetryStreamScope(), start)

    cursors = [int(e.stream_cursor) for e in events]
    assert cursors == sorted(cursors) and cursors[0] > int(start)
    assert {e.trace_id for e in events} == {f"trace_{2:032x}", f"trace_{3:032x}"}
    only_two = await store.get_events_after(TelemetryStreamScope(run_id="run_2"), start)
    assert {e.trace_id for e in only_two} == {f"trace_{2:032x}"}


@pytest.mark.asyncio
async def test_a_record_after_a_trace_ended_reaches_the_archive(tmp_path):
    store = _store(tmp_path)
    trace_id = await _record(store, 1)
    recorder = TelemetryRecorder(store)
    from omnicoreagent.core.telemetry.models import TelemetryEvent

    await store.append_event(
        trace_id,
        TelemetryEvent(trace_id=trace_id, event_type="telemetry_error", actor=TelemetryActor(type=ActorType.SYSTEM)),
    )
    await store.flush()
    del recorder

    assert store._inner._traces == {}
    trace = await store.get_trace(trace_id)
    assert trace.events[-1].event_type == "telemetry_error"
