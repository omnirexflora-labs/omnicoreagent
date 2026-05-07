from datetime import datetime
from pathlib import Path

import pytest

from omnicoreagent.core.workspace.factory import (
    clear_workspace_files_backend_cache,
    create_workspace_files_backend,
)
from omnicoreagent.core.workspace.tools import WorkspaceFilesTool
from omnicoreagent.core.workspace.files import WorkspaceFilesBackend
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage, S3WorkspaceStorage


class FakeS3Body:
    def __init__(self, data: bytes):
        self.data = data

    def read(self):
        return self.data


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "LastModified": datetime(2026, 1, 1),
        }
        return {"ETag": '"etag"'}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        item = self.objects[(Bucket, Key)]
        return {"Body": FakeS3Body(item["Body"])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        delimiter = kwargs.get("Delimiter")
        contents = []
        prefixes = set()
        for (bucket, key), item in self.objects.items():
            if bucket != Bucket or not key.startswith(Prefix):
                continue
            remainder = key[len(Prefix) :]
            if delimiter and delimiter in remainder:
                prefixes.add(Prefix + remainder.split(delimiter, 1)[0] + delimiter)
                continue
            contents.append({"Key": key, "LastModified": item["LastModified"]})
        return {
            "Contents": contents,
            "CommonPrefixes": [{"Prefix": prefix} for prefix in sorted(prefixes)],
        }

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def copy_object(self, Bucket, CopySource, Key, **kwargs):
        source = self.objects[(CopySource["Bucket"], CopySource["Key"])]
        self.objects[(Bucket, Key)] = dict(source)
        return {}

    def delete_objects(self, Bucket, Delete):
        for item in Delete["Objects"]:
            self.delete_object(Bucket=Bucket, Key=item["Key"])
        return {}


@pytest.fixture
def local_workspace_files(tmp_path):
    return WorkspaceFilesBackend(LocalWorkspaceStorage(tmp_path / "files"))


def test_workspace_files_create_append_overwrite_and_view(local_workspace_files, tmp_path):
    result = local_workspace_files.write("notes/today.md", "first", mode="create")
    assert "created" in result.lower()
    assert (tmp_path / "files" / "notes" / "today.md").read_text() == "first"

    assert "already exists" in local_workspace_files.write(
        "notes/today.md", "ignored", mode="create"
    ).lower()

    local_workspace_files.write("notes/today.md", "second", mode="append")
    assert "first\nsecond" == (tmp_path / "files" / "notes" / "today.md").read_text()

    local_workspace_files.write("notes/today.md", "final", mode="overwrite")
    assert "final" == (tmp_path / "files" / "notes" / "today.md").read_text()

    assert "final" in local_workspace_files.view("notes/today.md")
    assert "notes/" in local_workspace_files.view("")


def test_workspace_files_serializes_structured_content(local_workspace_files, tmp_path):
    local_workspace_files.write("data.json", {"name": "omni", "count": 2})

    assert '"name": "omni"' in (tmp_path / "files" / "data.json").read_text()


def test_workspace_files_views_empty_directory(local_workspace_files, tmp_path):
    (tmp_path / "files" / "empty").mkdir()

    assert "(empty)" in local_workspace_files.view("empty")


def test_workspace_files_replace_insert_delete_rename_and_clear(local_workspace_files, tmp_path):
    local_workspace_files.write("memo.txt", "hello world", mode="create")

    assert "replaced" in local_workspace_files.replace("memo.txt", "world", "agent").lower()
    assert "hello agent" == (tmp_path / "files" / "memo.txt").read_text()

    assert "inserted" in local_workspace_files.insert("memo.txt", 1, "top").lower()
    assert (tmp_path / "files" / "memo.txt").read_text().startswith("top\n")

    assert "renamed" in local_workspace_files.rename("memo.txt", "archive/memo.txt").lower()
    assert not (tmp_path / "files" / "memo.txt").exists()
    assert (tmp_path / "files" / "archive" / "memo.txt").is_file()

    assert "deleted" in local_workspace_files.delete("archive/memo.txt").lower()
    assert not (tmp_path / "files" / "archive" / "memo.txt").exists()

    local_workspace_files.write("one.txt", "1", mode="create")
    local_workspace_files.write("nested/two.txt", "2", mode="create")
    assert "cleared" in local_workspace_files.clear().lower()
    assert list((tmp_path / "files").iterdir()) == []


def test_workspace_files_rejects_path_traversal(local_workspace_files):
    result = local_workspace_files.write("../outside.txt", "bad", mode="create")

    assert "outside workspace namespace" in result


def test_workspace_files_uses_s3_compatible_workspace_storage():
    client = FakeS3Client()
    storage = S3WorkspaceStorage(
        bucket_name="bucket",
        prefix="workspace/files",
        client=client,
    )
    memory = WorkspaceFilesBackend(storage)

    assert "created" in memory.write("notes.txt", "hello", mode="create").lower()
    assert ("bucket", "workspace/files/notes.txt") in client.objects
    assert "hello" in memory.view("notes.txt")

    memory.write("nested/item.txt", "child", mode="create")
    assert "nested/" in memory.view("")

    assert "renamed" in memory.rename("notes.txt", "renamed.txt").lower()
    assert ("bucket", "workspace/files/renamed.txt") in client.objects

    assert "deleted" in memory.delete("renamed.txt").lower()
    assert ("bucket", "workspace/files/renamed.txt") not in client.objects


def test_create_workspace_files_backend_local_uses_workspace_files(monkeypatch, tmp_path):
    clear_workspace_files_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))

    backend = create_workspace_files_backend()

    assert isinstance(backend, WorkspaceFilesBackend)
    assert backend.base_dir == (tmp_path / "workspace" / "files").resolve()

    clear_workspace_files_backend_cache()


def test_create_workspace_files_backend_accepts_explicit_workspace_config(monkeypatch, tmp_path):
    clear_workspace_files_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "explicit-workspace"

    backend = create_workspace_files_backend(
        workspace_config=WorkspaceConfig(workspace_dir=workspace)
    )

    assert isinstance(backend, WorkspaceFilesBackend)
    assert backend.base_dir == (workspace / "files").resolve()

    clear_workspace_files_backend_cache()


def test_workspace_files_accepts_explicit_workspace_config(monkeypatch, tmp_path):
    clear_workspace_files_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "workspace-files-workspace"

    tool = WorkspaceFilesTool(workspace_config=WorkspaceConfig(workspace_dir=workspace))
    result = tool.write("note.txt", "hello", mode="create")

    assert "created" in result.lower()
    assert (workspace / "files" / "note.txt").read_text() == "hello"

    clear_workspace_files_backend_cache()


def test_create_workspace_files_backend_cache_respects_workspace_changes(monkeypatch, tmp_path):
    clear_workspace_files_backend_cache()
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(first_workspace))
    first = create_workspace_files_backend()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(second_workspace))
    second = create_workspace_files_backend()

    assert first is not second
    assert first.base_dir == (first_workspace / "files").resolve()
    assert second.base_dir == (second_workspace / "files").resolve()

    clear_workspace_files_backend_cache()


def test_create_workspace_files_backend_uses_workspace_backend_env(monkeypatch):
    clear_workspace_files_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_BACKEND", "s3")
    monkeypatch.setenv("AWS_S3_BUCKET", "bucket")

    backend = create_workspace_files_backend()

    assert isinstance(backend, WorkspaceFilesBackend)
    assert (
        backend.storage.location("note.txt")
        == "s3://bucket/workspace/files/note.txt"
    )

    clear_workspace_files_backend_cache()


def test_workspace_files_defaults_to_local(monkeypatch, tmp_path):
    clear_workspace_files_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))

    tool = WorkspaceFilesTool()
    result = tool.write("note.txt", "hello", mode="create")

    assert "created" in result.lower()
    assert Path(tmp_path / "workspace" / "files" / "note.txt").read_text() == "hello"

    clear_workspace_files_backend_cache()
