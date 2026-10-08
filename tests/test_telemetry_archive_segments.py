"""P7: a local archive packs trace bodies into segment files.

One file per finished trace was millions of inodes on a busy server (the third
soak: kernel slab grew with the file count while the process stayed flat). A
local archive now appends every body to a segment, one per writer process per
hour, and the index row names segment, offset and length. These tests hold the
layout, the replace and retention rules, the old per-trace files still being
read and pruned, object storage staying one object per trace, and three
processes sharing one directory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from omnicoreagent.core.telemetry.archive import TelemetryArchive
from omnicoreagent.core.telemetry.archive_index import (
    SqliteTelemetryIndex,
    SqlTelemetryIndex,
)
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
)
from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage

T0 = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)
BODY = re.compile(r"^segments/(\d{10})-([0-9a-f]{16})\.seg#(\d+):(\d+)$")


def _trace(number: int, *, run_id: str = "run") -> tuple[TelemetryTrace, dict[str, int]]:
    trace_id = f"trace_{number:032x}"
    span = TelemetrySpan(
        trace_id=trace_id,
        span_id=f"span_{number:032x}",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="steward"),
        started_at=T0 + timedelta(minutes=number),
    )
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=span.span_id,
        run_id=run_id,
        session_id="session",
        agent_id="steward",
        status=TraceStatus.COMPLETED,
        started_at=T0 + timedelta(minutes=number),
        ended_at=T0 + timedelta(minutes=number, seconds=30),
        spans=[span],
    )
    return trace, {}


def _segments(root: Path) -> list[Path]:
    return sorted((root / "bodies" / "segments").glob("*.seg"))


def _body(archive: TelemetryArchive, trace_id: str) -> str:
    return archive.index.body_of(trace_id)


def _files(root: Path) -> list[Path]:
    return [p for p in (root / "bodies").rglob("*") if p.is_file()]


@pytest.fixture(params=["sqlite", "sql"])
def make_index(request, tmp_path):
    made = []

    def build():
        if request.param == "sqlite":
            index = SqliteTelemetryIndex(tmp_path / "archive")
        else:
            index = SqlTelemetryIndex(f"sqlite:///{tmp_path / 'index.db'}")
        made.append(index)
        return index

    yield build
    for index in made:
        index.close()


@pytest.mark.asyncio
async def test_many_traces_are_one_segment_not_many_files(tmp_path, make_index):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    for number in range(1, 21):
        await archive.put(*_trace(number))

    assert len(_files(tmp_path / "archive")) == 1
    assert len(_segments(tmp_path / "archive")) == 1
    for number in range(1, 21):
        trace, _ = _trace(number)
        stored = await archive.get(trace.trace_id)
        assert stored is not None and stored[0].model_dump() == trace.model_dump()
    match = BODY.match(_body(archive, _trace(1)[0].trace_id))
    assert match and int(match.group(3)) == 0


@pytest.mark.asyncio
async def test_the_row_names_exactly_the_bytes_of_its_body(tmp_path, make_index):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    first, _ = _trace(1)
    second, cursors = _trace(2)
    await archive.put(first, {"event_a": 7})
    await archive.put(second, cursors)

    _, _, offset, length = BODY.match(_body(archive, second.trace_id)).groups()
    raw = _segments(tmp_path / "archive")[0].read_bytes()
    record = json.loads(raw[int(offset) : int(offset) + int(length)])
    assert record["trace"]["trace_id"] == second.trace_id
    assert int(offset) > 0


@pytest.mark.asyncio
async def test_a_partial_tail_does_not_corrupt_later_appends(tmp_path, make_index):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    first, cursors = _trace(1)
    await archive.put(first, cursors)
    segment = _segments(tmp_path / "archive")[0]

    # A crash mid-write leaves bytes no row points at, at the end of the file;
    # the writer reopens, as it does after any failed write.
    with open(segment, "ab") as handle:
        handle.write(b'{"trace": {"trace_id": "half')
    archive._segment_writer.close()

    second, _ = _trace(2)
    await archive.put(second, cursors)
    for trace in (first, second):
        stored = await archive.get(trace.trace_id)
        assert stored is not None and stored[0].trace_id == trace.trace_id


@pytest.mark.asyncio
async def test_a_failed_write_leaves_the_next_body_readable(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    archive = TelemetryArchive(root, index=SqliteTelemetryIndex(root))
    first, cursors = _trace(1)
    await archive.put(first, cursors)

    real_write = os.write
    calls = {"n": 0}

    def short_then_fail(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            real_write(fd, bytes(data)[:10])
            raise OSError("disk went away")
        return real_write(fd, data)

    monkeypatch.setattr("omnicoreagent.core.telemetry.segments.os.write", short_then_fail)
    broken, _ = _trace(2)
    with pytest.raises(OSError):
        await archive.put(broken, cursors)
    assert not await archive.contains(broken.trace_id)

    third, _ = _trace(3)
    await archive.put(third, cursors)
    assert (await archive.get(third.trace_id))[0].trace_id == third.trace_id
    assert (await archive.get(first.trace_id))[0].trace_id == first.trace_id
    archive.close()


@pytest.mark.asyncio
async def test_replacing_a_trace_appends_and_repoints(tmp_path, make_index):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    trace, cursors = _trace(1, run_id="before")
    await archive.put(trace, cursors)
    before = _body(archive, trace.trace_id)
    size = _segments(tmp_path / "archive")[0].stat().st_size

    trace.run_id = "after"
    await archive.put(trace, cursors)

    assert _body(archive, trace.trace_id) != before
    assert _segments(tmp_path / "archive")[0].stat().st_size > size
    assert (await archive.get(trace.trace_id))[0].run_id == "after"


def _row(trace_id: str, body: str) -> dict:
    return {
        "trace_id": trace_id,
        "status": "completed",
        "body": body,
        "bytes": 1,
        "payload_references": "[]",
    }


@pytest.mark.asyncio
async def test_a_segment_goes_when_no_row_points_into_it(tmp_path, make_index):
    root = tmp_path / "archive"
    segments = root / "bodies" / "segments"
    segments.mkdir(parents=True)
    # Written by a writer that is gone, in an hour long past.
    gone = segments / "2026010100-00000001deadbeef.seg"
    gone.write_bytes(b"x" * 20)
    index = make_index()
    index.put(_row("a", "segments/2026010100-00000001deadbeef.seg#0:10"))
    index.put(_row("b", "segments/2026010100-00000001deadbeef.seg#10:10"))

    archive = TelemetryArchive(root, index=index)
    await archive.remove({"a"})
    assert gone.exists(), "one row still points into it"
    await archive.remove({"b"})
    assert not gone.exists()


@pytest.mark.asyncio
async def test_a_segment_nothing_ever_pointed_at_is_swept_too(tmp_path, make_index):
    """A crash between the append and the index write leaves such a segment."""
    root = tmp_path / "archive"
    segments = root / "bodies" / "segments"
    segments.mkdir(parents=True)
    orphan = segments / "2026010100-00000002cafe0123.seg"
    orphan.write_bytes(b"orphan")
    archive = TelemetryArchive(root, index=make_index())
    await archive.remove({"nothing"})
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_the_segment_being_written_is_never_deleted(tmp_path, make_index):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    trace, cursors = _trace(1)
    await archive.put(trace, cursors)
    await archive.remove({trace.trace_id})

    # Nothing points into it, but this process may append to it next.
    assert len(_segments(tmp_path / "archive")) == 1
    again, _ = _trace(2)
    await archive.put(again, cursors)
    assert (await archive.get(again.trace_id)) is not None


@pytest.mark.asyncio
async def test_a_recent_unreferenced_segment_is_left_for_its_writer(tmp_path, make_index):
    """Another process's current segment has no row yet while its body is in flight."""
    root = tmp_path / "archive"
    segments = root / "bodies" / "segments"
    segments.mkdir(parents=True)
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    other = segments / f"{hour}-00000003abc12345.seg"
    other.write_bytes(b"in flight")
    archive = TelemetryArchive(root, index=make_index())
    await archive.remove({"nothing"})
    assert other.exists()


@pytest.mark.asyncio
async def test_old_per_trace_files_are_still_read_and_pruned(tmp_path, make_index):
    """An archive written by 0.5.1 and earlier: nothing is migrated."""
    root = tmp_path / "archive"
    storage = LocalWorkspaceStorage(root / "bodies")
    index = make_index()
    old_trace, _ = _trace(1, run_id="old")
    name = f"{old_trace.trace_id}.json"
    text = json.dumps({"trace": old_trace.model_dump(), "cursors": {"e": 5}})
    storage.write_text(name, text)
    index.put(
        {
            "trace_id": old_trace.trace_id,
            "run_id": "old",
            "status": "completed",
            "first_cursor": 5,
            "last_cursor": 5,
            "payload_references": "[]",
            "body": name,
            "bytes": len(text),
        }
    )

    archive = TelemetryArchive(root, index=index)
    stored = await archive.get(old_trace.trace_id)
    assert stored is not None and stored[0].run_id == "old" and stored[1] == {"e": 5}

    new, cursors = _trace(2)
    await archive.put(new, cursors)
    assert (root / "bodies" / name).exists(), "the old file is not rewritten"
    assert len(_segments(root)) == 1

    await archive.remove({old_trace.trace_id})
    assert not (root / "bodies" / name).exists()
    assert await archive.get(old_trace.trace_id) is None
    assert (await archive.get(new.trace_id)) is not None


class _ObjectStorage:
    """Stands in for a bucket: named objects, no append, no inodes."""

    def __init__(self):
        self.objects: dict[str, str] = {}

    def write_text(self, path, text, **_):
        self.objects[str(path)] = text

    def read_text(self, path, **_):
        return self.objects[str(path)]

    def delete(self, path, **_):
        del self.objects[str(path)]


@pytest.mark.asyncio
async def test_object_storage_keeps_one_object_per_trace(tmp_path):
    bucket = _ObjectStorage()
    archive = TelemetryArchive(tmp_path / "archive", bodies=bucket)
    for number in (1, 2, 3):
        await archive.put(*_trace(number))
    assert sorted(bucket.objects) == [f"trace_{n:032x}.json" for n in (1, 2, 3)]
    assert (await archive.get(_trace(2)[0].trace_id)) is not None
    await archive.remove({_trace(2)[0].trace_id})
    assert len(bucket.objects) == 2
    archive.close()


_WRITER = textwrap.dedent(
    """
    import asyncio, sys
    from pathlib import Path
    sys.path.insert(0, {tests!r})
    from test_telemetry_archive_segments import _trace
    from omnicoreagent.core.telemetry.archive import TelemetryArchive
    from omnicoreagent.core.telemetry.archive_index import SqliteTelemetryIndex
    from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage

    async def main(base, start, count):
        root = Path(base)
        archive = TelemetryArchive(
            root, bodies=LocalWorkspaceStorage(root / "shared"),
            index=SqliteTelemetryIndex(root),
        )
        for number in range(start, start + count):
            await archive.put(*_trace(number))
        archive.close()

    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
    """
)


@pytest.mark.asyncio
async def test_three_processes_sharing_a_directory_do_not_corrupt_each_other(tmp_path):
    script = tmp_path / "writer.py"
    script.write_text(_WRITER.format(tests=str(Path(__file__).parent)))
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), str(tmp_path / "archive"), str(start), "60"]
        )
        for start in (1, 1001, 2001)
    ]
    for process in processes:
        assert process.wait(timeout=120) == 0

    root = tmp_path / "archive"
    reader = TelemetryArchive(
        root, bodies=LocalWorkspaceStorage(root / "shared"), index=SqliteTelemetryIndex(root)
    )
    segments = sorted((root / "shared" / "segments").glob("*.seg"))
    assert len(segments) == 3, "writers never share a segment"
    for start in (1, 1001, 2001):
        for number in range(start, start + 60):
            trace, _ = _trace(number)
            stored = await reader.get(trace.trace_id)
            assert stored is not None and stored[0].trace_id == trace.trace_id
    reader.close()


@pytest.mark.asyncio
async def test_a_forked_child_writes_a_segment_of_its_own(tmp_path):
    archive = TelemetryArchive(tmp_path / "archive", index=SqliteTelemetryIndex(tmp_path / "archive"))
    await archive.put(*_trace(1))
    writer = archive._segment_writer
    parent_name = writer.writer

    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child appends once and reports its writer id
        try:
            os.write(write_end, (writer.append(b"child") and writer.writer).encode())
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
    child_name = os.read(read_end, 64).decode()
    assert child_name and child_name != parent_name
    assert len(_segments(tmp_path / "archive")) == 2
    archive.close()
