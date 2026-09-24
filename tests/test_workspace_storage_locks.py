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
