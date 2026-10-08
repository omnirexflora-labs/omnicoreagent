"""Segment files: trace bodies packed, many to a file.

Production readiness plan, P7. The archive wrote one file per finished trace,
about 4.7 a visit, kept for the retention window: millions of files, and on the
third soak the kernel's inode and dentry slabs grew with them while the
process's own memory stayed flat. A local archive now appends each body to a
segment instead.

A segment is ``segments/<UTC hour>-<writer>.seg`` under the bodies directory:
one per writer per hour. The writer is unique to one archive instance (the
process id and a random token, sixteen hex digits), so two writers never share a segment and appending
needs no lock. A body is addressed by where it sits::

    segments/2026100814-00001092a1b2c3d4.seg#1048576:2310
    ^ segment                           ^ offset ^ length

and read back with one positioned read of exactly that range. A name with no
``#`` is an older per-trace file (``<trace_id>.json``), read and deleted the
way it always was.

Nothing here calls ``fsync``. A body is rebuildable telemetry, and a sync per
body is what the first design paid for and the soak did not need.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEGMENT_DIRECTORY = "segments"

_NAME = re.compile(r"^segments/(\d{10})-([0-9a-f]{16})\.seg#(\d+):(\d+)$")
_FILE = re.compile(r"^(\d{10})-([0-9a-f]{16})\.seg$")
# "segments/" + hour + "-" + writer + ".seg". The writer id is fixed-width so
# an index can cut a segment's name out of a body with a plain substring, the
# same in every database, and learn which segments are referenced in one scan.
SEGMENT_NAME_LENGTH = len("segments/") + 10 + 1 + 16 + len(".seg")
_HOUR = "%Y%m%d%H"


def is_segment_body(body: str) -> bool:
    return _NAME.match(body) is not None


def segment_of(body: str) -> str | None:
    """The segment's file name inside a segment body name, else ``None``."""
    found = _NAME.match(body)
    return f"segments/{found.group(1)}-{found.group(2)}.seg" if found else None


def read_body(root: Path, body: str) -> bytes:
    """The bytes a segment body name points at.

    The name is parsed against a strict pattern before any path is built, so a
    row cannot send a read outside the segments directory. A segment shorter
    than the name says (a copy cut short, a lost tail) is an error, never a
    quietly short body.
    """
    found = _NAME.match(body)
    if found is None:
        raise ValueError(f"not a segment body name: {body!r}")
    hour, writer, offset, length = found.groups()
    path = root / SEGMENT_DIRECTORY / f"{hour}-{writer}.seg"
    descriptor = os.open(path, os.O_RDONLY)
    try:
        data = os.pread(descriptor, int(length), int(offset))
    finally:
        os.close(descriptor)
    if len(data) != int(length):
        raise OSError(f"segment {path.name} is shorter than {body!r} says")
    return data


class SegmentWriter:
    """Appends bodies to this writer's segment for the current hour."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._new_writer_id()
        self._guard = threading.Lock()
        self._descriptor: int | None = None
        self._hour: str | None = None
        self._position = 0

    def _new_writer_id(self) -> None:
        self._pid = os.getpid()
        self.writer = f"{self._pid & 0xFFFFFFFF:08x}{secrets.token_hex(4)}"

    @property
    def current(self) -> str | None:
        """The segment file name being appended to now, if any."""
        with self._guard:
            if self._descriptor is None:
                return None
            return f"{self._hour}-{self.writer}.seg"

    def append(self, data: bytes) -> str:
        """Append ``data`` whole and return the body name that addresses it."""
        with self._guard:
            if os.getpid() != self._pid:
                # A forked child inherits this writer, its descriptor and its
                # idea of the position. Parent and child appending to one
                # segment is the sharing this design rules out, so the child
                # becomes a writer of its own.
                self._close()
                self._new_writer_id()
            hour = datetime.now(timezone.utc).strftime(_HOUR)
            if self._descriptor is None or hour != self._hour:
                self._open(hour)
            offset = self._position
            try:
                view = memoryview(data)
                while view:
                    written = os.write(self._descriptor, view)
                    view = view[written:]
            except BaseException:
                # How much of the body landed is unknown, and the next body
                # must not be addressed from a guess. Close, and the next
                # append starts from the file's real size: the stray bytes
                # stay behind, and no row points at them.
                self._close()
                raise
            self._position = offset + len(data)
            return f"{SEGMENT_DIRECTORY}/{hour}-{self.writer}.seg#{offset}:{len(data)}"

    def close(self) -> None:
        with self._guard:
            self._close()

    def _open(self, hour: str) -> None:
        self._close()
        directory = self.root / SEGMENT_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        # O_APPEND: every write lands at the end of the file whatever this
        # writer believes the position is, so a stale belief can never
        # overwrite a body. The size read here is where the first body goes.
        descriptor = os.open(
            directory / f"{hour}-{self.writer}.seg",
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        self._descriptor = descriptor
        self._hour = hour
        self._position = os.fstat(descriptor).st_size

    def _close(self) -> None:
        if self._descriptor is not None:
            try:
                os.close(self._descriptor)
            except OSError:
                pass
        self._descriptor = None


def deletable_segments(root: Path, *, keep: str | None, now: datetime | None = None) -> list[str]:
    """Segment names old enough that no writer can still be appending to one.

    A writer moves to a new segment when the hour turns, so a segment from the
    hour before the previous one is finished. The current and previous hours
    are left alone: another process's body may be appended and not yet in the
    index, and sweeping its segment then would lose that body. ``keep`` is this
    writer's own current segment, never offered.
    """
    directory = root / SEGMENT_DIRECTORY
    if not directory.is_dir():
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=1)).strftime(_HOUR)
    names = []
    for entry in os.listdir(directory):
        found = _FILE.match(entry)
        if found is None or entry == keep or found.group(1) >= cutoff:
            continue
        names.append(f"{SEGMENT_DIRECTORY}/{entry}")
    return sorted(names)
