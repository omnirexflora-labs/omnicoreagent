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


def test_an_absolute_path_outside_the_root_stays_inside_the_workspace(tmp_path):
    root = tmp_path / "app"
    storage = LocalWorkspaceStorage(root)

    storage.write_text("/etc/passwd-copy", "x")  # not under the root: relative, as before

    assert (root / "etc" / "passwd-copy").read_text() == "x"
