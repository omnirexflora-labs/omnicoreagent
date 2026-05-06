from datetime import datetime
from pathlib import Path

import pytest

from omnicoreagent.core.tools.memory_tool.factory import (
    clear_backend_cache,
    create_memory_backend,
)
from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool
from omnicoreagent.core.tools.memory_tool.storage import WorkspaceMemoryBackend
from omnicoreagent.core.workspace_config import WorkspaceConfig
from omnicoreagent.core.workspace_storage import LocalWorkspaceStorage, S3WorkspaceStorage


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
def local_memory(tmp_path):
    return WorkspaceMemoryBackend(LocalWorkspaceStorage(tmp_path / "memories"))


def test_workspace_memory_create_append_overwrite_and_view(local_memory, tmp_path):
    result = local_memory.create_update("notes/today.md", "first", mode="create")
    assert "created" in result.lower()
    assert (tmp_path / "memories" / "notes" / "today.md").read_text() == "first"

    assert "already exists" in local_memory.create_update(
        "notes/today.md", "ignored", mode="create"
    ).lower()

    local_memory.create_update("notes/today.md", "second", mode="append")
    assert "first\nsecond" == (tmp_path / "memories" / "notes" / "today.md").read_text()

    local_memory.create_update("notes/today.md", "final", mode="overwrite")
    assert "final" == (tmp_path / "memories" / "notes" / "today.md").read_text()

    assert "final" in local_memory.view("notes/today.md")
    assert "notes/" in local_memory.view("")


def test_workspace_memory_serializes_structured_content(local_memory, tmp_path):
    local_memory.create_update("data.json", {"name": "omni", "count": 2})

    assert '"name": "omni"' in (tmp_path / "memories" / "data.json").read_text()


def test_workspace_memory_views_empty_directory(local_memory, tmp_path):
    (tmp_path / "memories" / "empty").mkdir()

    assert "(empty)" in local_memory.view("empty")


def test_workspace_memory_replace_insert_delete_rename_and_clear(local_memory, tmp_path):
    local_memory.create_update("memo.txt", "hello world", mode="create")

    assert "replaced" in local_memory.str_replace("memo.txt", "world", "agent").lower()
    assert "hello agent" == (tmp_path / "memories" / "memo.txt").read_text()

    assert "inserted" in local_memory.insert("memo.txt", 1, "top").lower()
    assert (tmp_path / "memories" / "memo.txt").read_text().startswith("top\n")

    assert "renamed" in local_memory.rename("memo.txt", "archive/memo.txt").lower()
    assert not (tmp_path / "memories" / "memo.txt").exists()
    assert (tmp_path / "memories" / "archive" / "memo.txt").is_file()

    assert "deleted" in local_memory.delete("archive/memo.txt").lower()
    assert not (tmp_path / "memories" / "archive" / "memo.txt").exists()

    local_memory.create_update("one.txt", "1", mode="create")
    local_memory.create_update("nested/two.txt", "2", mode="create")
    assert "cleared" in local_memory.clear_all_memory().lower()
    assert list((tmp_path / "memories").iterdir()) == []


def test_workspace_memory_rejects_path_traversal(local_memory):
    result = local_memory.create_update("../outside.txt", "bad", mode="create")

    assert "outside workspace namespace" in result


def test_workspace_memory_uses_s3_compatible_workspace_storage():
    client = FakeS3Client()
    storage = S3WorkspaceStorage(
        bucket_name="bucket",
        prefix="workspace/memories",
        client=client,
    )
    memory = WorkspaceMemoryBackend(storage)

    assert "created" in memory.create_update("notes.txt", "hello", mode="create").lower()
    assert ("bucket", "workspace/memories/notes.txt") in client.objects
    assert "hello" in memory.view("notes.txt")

    memory.create_update("nested/item.txt", "child", mode="create")
    assert "nested/" in memory.view("")

    assert "renamed" in memory.rename("notes.txt", "renamed.txt").lower()
    assert ("bucket", "workspace/memories/renamed.txt") in client.objects

    assert "deleted" in memory.delete("renamed.txt").lower()
    assert ("bucket", "workspace/memories/renamed.txt") not in client.objects


def test_create_memory_backend_local_uses_workspace_memories(monkeypatch, tmp_path):
    clear_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))

    backend = create_memory_backend()

    assert isinstance(backend, WorkspaceMemoryBackend)
    assert backend.base_dir == (tmp_path / "workspace" / "memories").resolve()

    clear_backend_cache()


def test_create_memory_backend_accepts_explicit_workspace_config(monkeypatch, tmp_path):
    clear_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "explicit-workspace"

    backend = create_memory_backend(
        workspace_config=WorkspaceConfig(workspace_dir=workspace)
    )

    assert isinstance(backend, WorkspaceMemoryBackend)
    assert backend.base_dir == (workspace / "memories").resolve()

    clear_backend_cache()


def test_memory_tool_accepts_explicit_workspace_config(monkeypatch, tmp_path):
    clear_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "memory-tool-workspace"

    tool = MemoryTool(workspace_config=WorkspaceConfig(workspace_dir=workspace))
    result = tool.create_update("note.txt", "hello", mode="create")

    assert "created" in result.lower()
    assert (workspace / "memories" / "note.txt").read_text() == "hello"

    clear_backend_cache()


def test_create_memory_backend_cache_respects_workspace_changes(monkeypatch, tmp_path):
    clear_backend_cache()
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(first_workspace))
    first = create_memory_backend()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(second_workspace))
    second = create_memory_backend()

    assert first is not second
    assert first.base_dir == (first_workspace / "memories").resolve()
    assert second.base_dir == (second_workspace / "memories").resolve()

    clear_backend_cache()


def test_create_memory_backend_uses_workspace_backend_env(monkeypatch):
    clear_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_BACKEND", "s3")
    monkeypatch.setenv("AWS_S3_BUCKET", "bucket")

    backend = create_memory_backend()

    assert isinstance(backend, WorkspaceMemoryBackend)
    assert (
        backend.storage.location("note.txt")
        == "s3://bucket/workspace/memories/note.txt"
    )

    clear_backend_cache()


def test_memory_tool_defaults_to_local(monkeypatch, tmp_path):
    clear_backend_cache()
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))

    tool = MemoryTool()
    result = tool.create_update("note.txt", "hello", mode="create")

    assert "created" in result.lower()
    assert Path(tmp_path / "workspace" / "memories" / "note.txt").read_text() == "hello"

    clear_backend_cache()
