from pathlib import Path
from datetime import datetime

from omnicoreagent.core.tool_response_offloader import OffloadConfig
from omnicoreagent.core.tools.memory_tool.factory import (
    clear_backend_cache,
    create_memory_backend,
)
from omnicoreagent.core.workspace import (
    ensure_workspace,
    get_artifacts_dir,
    get_config_dir,
    get_memories_dir,
    get_workspace_dir,
    resolve_workspace_paths,
)
from omnicoreagent.core.workspace_storage import LocalWorkspaceStorage
from omnicoreagent.core.workspace_storage import S3WorkspaceStorage


def test_workspace_paths_resolve_from_current_environment(monkeypatch, tmp_path):
    workspace = tmp_path / "agent_workspace"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))

    assert Path(get_workspace_dir()) == workspace
    assert Path(get_artifacts_dir()) == workspace / "artifacts"
    assert Path(get_memories_dir()) == workspace / "memories"
    assert Path(get_config_dir()) == workspace / "config"


def test_workspace_paths_support_explicit_subdir_overrides(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifact_store"
    memories = tmp_path / "memory_store"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("OMNICOREAGENT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("OMNICOREAGENT_MEMORY_DIR", str(memories))

    paths = resolve_workspace_paths()

    assert paths.root == workspace
    assert paths.artifacts == artifacts
    assert paths.memories == memories
    assert paths.config == workspace / "config"


def test_ensure_workspace_creates_runtime_directories(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))

    paths = ensure_workspace()

    assert paths.root.is_dir()
    assert paths.artifacts.is_dir()
    assert paths.memories.is_dir()
    assert paths.config.is_dir()


def test_offload_config_reads_workspace_environment_at_instantiation(
    monkeypatch, tmp_path
):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(first_workspace))
    first = OffloadConfig()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(second_workspace))
    second = OffloadConfig()

    assert Path(first.storage_dir) == first_workspace / "artifacts"
    assert Path(second.storage_dir) == second_workspace / "artifacts"


def test_local_memory_backend_cache_respects_workspace_changes(monkeypatch, tmp_path):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    clear_backend_cache()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(first_workspace))
    first = create_memory_backend()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(second_workspace))
    second = create_memory_backend()

    assert first is not second
    assert first.base_dir == (first_workspace / "memories").resolve()
    assert second.base_dir == (second_workspace / "memories").resolve()

    clear_backend_cache()


def test_local_workspace_storage_keeps_paths_inside_root(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "workspace")

    storage.write_text("nested/result.txt", "hello")

    assert storage.read_text("nested/result.txt") == "hello"
    assert (tmp_path / "workspace" / "nested" / "result.txt").is_file()


def test_local_workspace_storage_rejects_path_traversal(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "workspace")

    try:
        storage.write_text("../outside.txt", "bad")
    except ValueError as exc:
        assert "outside workspace namespace" in str(exc)
    else:
        raise AssertionError("Path traversal should be rejected")

    assert not (tmp_path / "outside.txt").exists()


def test_local_workspace_storage_strips_namespace_prefix(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "workspace" / "memories")

    storage.write_text("memories/notes/today.md", "note", strip_prefixes=("memories",))

    assert storage.read_text("notes/today.md") == "note"
    assert not (tmp_path / "workspace" / "memories" / "memories").exists()


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

    def get_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"Body": FakeS3Body(item["Body"])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        contents = []
        for (bucket, key), item in self.objects.items():
            if bucket == Bucket and key.startswith(Prefix):
                contents.append(
                    {
                        "Key": key,
                        "LastModified": item["LastModified"],
                    }
                )
        return {"Contents": contents}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def copy_object(self, Bucket, CopySource, Key, **kwargs):
        source = self.objects[(CopySource["Bucket"], CopySource["Key"])]
        self.objects[(Bucket, Key)] = dict(source)

    def delete_objects(self, Bucket, Delete):
        for item in Delete["Objects"]:
            self.delete_object(Bucket=Bucket, Key=item["Key"])


def test_s3_workspace_storage_reads_writes_and_lists_files():
    client = FakeS3Client()
    storage = S3WorkspaceStorage(
        bucket_name="bucket",
        prefix="workspace/artifacts",
        client=client,
    )

    storage.write_text("result.txt", "hello")

    assert storage.exists("result.txt")
    assert storage.read_text("result.txt") == "hello"
    assert storage.location("result.txt") == "s3://bucket/workspace/artifacts/result.txt"
    assert [item.name for item in storage.list_files()] == ["result.txt"]


def test_s3_workspace_storage_rejects_path_traversal():
    storage = S3WorkspaceStorage(bucket_name="bucket", client=FakeS3Client())

    try:
        storage.write_text("../outside.txt", "bad")
    except ValueError as exc:
        assert "outside workspace namespace" in str(exc)
    else:
        raise AssertionError("Path traversal should be rejected")


def test_s3_workspace_storage_delete_and_rename():
    client = FakeS3Client()
    storage = S3WorkspaceStorage(bucket_name="bucket", prefix="workspace", client=client)

    storage.write_text("old.txt", "content")
    storage.rename("old.txt", "new.txt")

    assert not storage.exists("old.txt")
    assert storage.read_text("new.txt") == "content"

    storage.delete("new.txt")
    assert not storage.exists("new.txt")
