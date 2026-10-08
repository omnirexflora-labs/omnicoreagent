"""Local storage keeps its locks out of the namespace — somewhere it may write.

Found while bringing up two server processes sharing one directory of trace
bodies (scale plan S4). The lock for a file goes beside the storage root, so it
is never listed as workspace content: for a root of ``/shared`` that is
``/.shared.locks``, at the top of the filesystem, which a container's user
cannot create. Every write failed with ``Permission denied``, and because
telemetry must not fail a run, nothing was archived and nothing said why.

The lock still goes outside the namespace. When that place cannot be written,
it goes to one derived from the root under the system temp directory — the same
path for every process using that root, so they still lock against each other.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage


def test_locks_live_beside_the_root_when_that_is_writable(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "bodies")
    storage.write_text("trace.json", "{}")

    assert storage.read_text("trace.json") == "{}"
    assert (tmp_path / ".bodies.locks").is_dir()
    # And the lock is not part of the namespace.
    assert [item.name for item in storage.list_files()] == ["trace.json"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write any directory")
def test_a_root_whose_parent_is_not_writable_can_still_be_written(tmp_path):
    parent = tmp_path / "mount"
    root = parent / "bodies"
    root.mkdir(parents=True)
    parent.chmod(0o500)  # readable and searchable, not writable
    try:
        storage = LocalWorkspaceStorage(root)
        storage.write_text("trace.json", '{"trace": 1}')

        assert storage.read_text("trace.json") == '{"trace": 1}'
        assert not (parent / ".bodies.locks").exists()
        # The fallback is derived from the root, so another process with the
        # same root locks against the same directory.
        again = LocalWorkspaceStorage(root)
        assert again._lock_directory() == storage._lock_directory()
        assert Path(tempfile.gettempdir()) in storage._lock_directory().parents
        assert [item.name for item in storage.list_files()] == ["trace.json"]
    finally:
        parent.chmod(0o700)


def test_a_crowd_of_files_shares_a_bounded_set_of_lock_files(tmp_path):
    """Soak 3 (2026-10-08): the support desk's trace archive left a lock file per body.

    The desk's memory rose about 2 MiB a minute while the Python heap stayed
    flat: the rest was the kernel's cache of the inodes and names of files made
    for every trace, and half of them were locks that nothing ever removed. A
    lock now belongs to a stripe of the names, so the lock files are a fixed
    number however many bodies there are.
    """
    storage = LocalWorkspaceStorage(tmp_path / "bodies")
    for number in range(600):
        storage.write_text(f"trace_{number}.json", "{}")

    locks = list((tmp_path / ".bodies.locks").iterdir())
    assert 0 < len(locks) <= LocalWorkspaceStorage.LOCK_STRIPES
    assert len(storage.list_files()) == 600


def test_two_names_on_one_stripe_can_be_renamed_both_ways(tmp_path, monkeypatch):
    # With one stripe every name shares a lock: a rename takes the lock of the
    # old and of the new name, and must not wait for itself.
    monkeypatch.setattr(LocalWorkspaceStorage, "LOCK_STRIPES", 1)
    storage = LocalWorkspaceStorage(tmp_path / "bodies")
    storage.write_text("a.json", "a")
    storage.rename("a.json", "b.json")
    storage.write_text("a.json", "again")
    storage.rename("b.json", "c.json")

    assert storage.read_text("c.json") == "a" and storage.read_text("a.json") == "again"
    storage.delete("a.json")
    storage.clear()
    assert storage.list_files() == []


def test_a_write_looks_up_its_temp_name_only_when_it_failed(tmp_path, monkeypatch):
    """Looking up a name the write has already renamed away leaves the kernel a
    negative entry for it, and every temp name is new: one for every body, never
    reused (soak 3, 2026-10-08). Only a failed write needs to clean up."""
    storage = LocalWorkspaceStorage(tmp_path / "bodies")
    looked_up: list[str] = []
    real_unlink = Path.unlink
    monkeypatch.setattr(
        Path, "unlink", lambda self, *a, **k: (looked_up.append(self.name), real_unlink(self, *a, **k))[1]
    )

    storage.write_text("trace.json", "{}")
    assert looked_up == []

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError):
        storage.write_text("other.json", "{}")
    assert len(looked_up) == 1 and looked_up[0].endswith(".tmp")
    assert [item.name for item in (tmp_path / "bodies").iterdir()] == ["trace.json"]
