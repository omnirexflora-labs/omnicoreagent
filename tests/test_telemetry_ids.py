"""Telemetry IDs cost no system call each (R4 of the support desk P6).

Every event and span gets an ID, about forty to a step, and ``uuid4()`` asks
the operating system for random bytes each time. A thread that returns from a
system call has to win the interpreter lock back, and with the database
threads busy that wait is up to the switch interval: sampled on a loaded
machine (2026-10-07) ID generation was a fifth of the event loop's busy time.
The bytes now come from one read of the system's source for many IDs. The IDs
are what they were: random, and shaped like a version-4 UUID.
"""

from __future__ import annotations

import os
import re
import threading
import uuid

from omnicoreagent.core.telemetry.models import telemetry_id

SHAPE = re.compile(r"^(span|trace|event)_[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$")


def test_ids_are_unique_and_shaped_like_a_version_4_uuid():
    ids = [telemetry_id(prefix) for prefix in ("span", "trace", "event") * 2000]
    assert len(set(ids)) == len(ids)
    assert all(SHAPE.match(one) for one in ids), next(one for one in ids if not SHAPE.match(one))
    # The part after the prefix is a valid UUID.
    assert uuid.UUID(ids[0].split("_", 1)[1]).version == 4


def test_a_thousand_ids_read_the_system_source_a_handful_of_times(monkeypatch):
    reads = []
    real = os.urandom
    monkeypatch.setattr(os, "urandom", lambda n: (reads.append(n), real(n))[1])
    for _ in range(1000):
        telemetry_id("span")
    assert 1 <= len(reads) <= 8, reads


def test_threads_never_share_the_same_bytes():
    seen: list[str] = []
    lock = threading.Lock()

    def make():
        mine = [telemetry_id("span") for _ in range(2000)]
        with lock:
            seen.extend(mine)

    threads = [threading.Thread(target=make) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == 16000 and len(set(seen)) == 16000


def test_a_forked_child_does_not_replay_its_parents_ids():
    if not hasattr(os, "fork"):
        return
    read, write = os.pipe()
    telemetry_id("span")  # fills the pool in this process
    pid = os.fork()
    if pid == 0:  # the child
        os.close(read)
        os.write(write, telemetry_id("span").encode())
        os._exit(0)
    os.close(write)
    child_first = os.read(read, 100).decode()
    os.waitpid(pid, 0)
    parent_next = telemetry_id("span")
    assert child_first != parent_next
