"""An absolute path inside the workspace means that file, for storage and policy alike.

The 0.5.0rc5 gate (a Terminal-Bench trial): with the workspace files root at
/app, `write_file("/app/ssl/verification.txt")` created /app/app/ssl/...
and `read_file("/app/ssl/...")` found nothing: a leading "/" was dropped, so
the path was taken as relative. Policy must see the same file the storage
touches, or a rule on "secret/*" would miss "/app/secret/x".
"""

from __future__ import annotations

from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage
from omnicoreagent.governance.capabilities import tool_authority_requests


def test_an_absolute_path_under_the_root_is_that_file(tmp_path):
    root = tmp_path / "app"
    storage = LocalWorkspaceStorage(root)

    storage.write_text(str(root / "ssl" / "verification.txt"), "ok")

    assert (root / "ssl" / "verification.txt").read_text() == "ok"
    assert not (root / str(root).lstrip("/")).exists(), "not re-rooted under itself"
    assert storage.read_text(str(root / "ssl" / "verification.txt")) == "ok"


def test_policy_sees_the_path_the_storage_writes(tmp_path):
    root = tmp_path / "app"
    LocalWorkspaceStorage(root)

    (request,) = tool_authority_requests(
        tool_name="write_file", tool_args={"path": str(root / "secret" / "key.txt"), "content": "x"},
        tool_provider="workspace",
    )
    assert request.target.path == "secret/key.txt"


def test_an_absolute_path_outside_the_root_is_refused(tmp_path):
    # The 0.5.0rc6 gate: with the root at /app, write_file("/tmp/scratch.txt")
    # wrote /app/tmp/scratch.txt, told the model so, and the record said
    # /tmp/scratch.txt; the docs say a path leading outside is refused.
    import pytest

    root = tmp_path / "app"
    storage = LocalWorkspaceStorage(root)

    with pytest.raises(ValueError, match="outside the workspace"):
        storage.write_text("/tmp/scratch.txt", "x")
    assert not (root / "tmp").exists()
    with pytest.raises(ValueError, match="outside the workspace"):
        tool_authority_requests(tool_name="read_file", tool_args={"path": "/etc/os-release"},
                                tool_provider="workspace")


def test_the_root_itself_and_a_files_prefix_still_mean_the_workspace(tmp_path):
    root = tmp_path / "app"
    storage = LocalWorkspaceStorage(root)
    storage.write_text("notes.txt", "hi")

    assert storage.resolve("/") == root.resolve()
    (request,) = tool_authority_requests(tool_name="read_file", tool_args={"path": "/files/notes.txt"},
                                         tool_provider="workspace")
    assert request.target.path == "notes.txt"
