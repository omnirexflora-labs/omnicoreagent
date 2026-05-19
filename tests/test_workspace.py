from pathlib import Path
from datetime import datetime

from omnicoreagent.core.workspace.factory import (
    clear_workspace_files_backend_cache,
    create_workspace_files_backend,
)
from omnicoreagent.core.workspace import (
    ensure_workspace,
    get_artifacts_dir,
    get_config_dir,
    get_workspace_files_dir,
    get_workspace_dir,
)
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.manager import Workspace
from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage
from omnicoreagent.core.workspace.storage import R2WorkspaceStorage
from omnicoreagent.core.workspace.storage import S3WorkspaceStorage
from omnicoreagent.core.workspace.storage import create_workspace_storage


def test_workspace_paths_resolve_from_current_environment(monkeypatch, tmp_path):
    workspace = tmp_path / "agent_workspace"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))

    assert Path(get_workspace_dir()) == workspace
    assert Path(get_artifacts_dir()) == workspace / "artifacts"
    assert Path(get_workspace_files_dir()) == workspace / "files"
    assert Path(get_config_dir()) == workspace / "config"


def test_workspace_storage_namespaces_share_one_workspace_root(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))

    artifacts = create_workspace_storage(namespace="artifacts")
    workspace_files = create_workspace_storage(namespace="files")

    assert isinstance(artifacts, LocalWorkspaceStorage)
    assert isinstance(workspace_files, LocalWorkspaceStorage)
    assert artifacts.root == (workspace / "artifacts").resolve()
    assert workspace_files.root == (workspace / "files").resolve()


def test_workspace_facade_exposes_files_and_artifacts(tmp_path):
    workspace_root = tmp_path / "workspace"

    workspace = Workspace.from_config(
        WorkspaceConfig(workspace_dir=workspace_root)
    ).ensure()
    workspace.files.write_text("notes/todo.md", "todo")
    workspace.artifacts.write_text("tool_output.txt", "result")

    assert (workspace_root / "files" / "notes" / "todo.md").read_text() == "todo"
    assert (workspace_root / "artifacts" / "tool_output.txt").read_text() == "result"


def test_workspace_config_from_env_normalizes_values(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_BACKEND", " S3 ")
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_PREFIX", "/agent-workspace/")
    monkeypatch.setenv("AWS_S3_BUCKET", "bucket")

    config = WorkspaceConfig.from_env()

    assert config.workspace_backend == "s3"
    assert config.workspace_dir == str(tmp_path / "workspace")
    assert config.namespace_prefix("artifacts") == "agent-workspace/artifacts"
    assert config.cache_key(namespace="artifacts") == (
        "s3",
        "bucket",
        None,
        None,
        "agent-workspace/artifacts",
    )


def test_workspace_config_from_env_normalizes_r2_values(monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_BACKEND", " R2 ")
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_PREFIX", "/agent-workspace/")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_ACCOUNT_ID", "account")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")

    config = WorkspaceConfig.from_env()

    assert config.workspace_backend == "r2"
    assert config.namespace_prefix("files") == "agent-workspace/files"
    assert config.cache_key(namespace="files") == (
        "r2",
        "bucket",
        "account",
        "agent-workspace/files",
    )


def test_workspace_config_uses_workspace_backend_name_only():
    try:
        WorkspaceConfig(backend="s3")
    except TypeError as exc:
        assert "backend" in str(exc)
    else:
        raise AssertionError("WorkspaceConfig must not accept generic backend")


def test_workspace_storage_accepts_explicit_local_config(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "explicit-workspace"
    config = WorkspaceConfig(workspace_dir=workspace)

    storage = create_workspace_storage(namespace="artifacts", config=config)

    assert isinstance(storage, LocalWorkspaceStorage)
    assert storage.root == (workspace / "artifacts").resolve()


def test_workspace_storage_accepts_explicit_s3_config(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    captured = {}

    class FakeStorage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "omnicoreagent.core.workspace.storage.S3WorkspaceStorage",
        FakeStorage,
    )

    storage = create_workspace_storage(
        namespace="files",
        config=WorkspaceConfig(
            workspace_backend="s3",
            prefix="agent",
            s3_bucket="bucket",
            aws_region="us-east-1",
            aws_endpoint_url="https://example.test",
        ),
    )

    assert isinstance(storage, FakeStorage)
    assert captured == {
        "bucket_name": "bucket",
        "prefix": "agent/files",
        "region": "us-east-1",
        "aws_access_key_id": None,
        "aws_secret_access_key": None,
        "endpoint_url": "https://example.test",
    }


def test_workspace_storage_accepts_explicit_r2_config(monkeypatch):
    captured = {}

    class FakeStorage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "omnicoreagent.core.workspace.storage.R2WorkspaceStorage",
        FakeStorage,
    )

    storage = create_workspace_storage(
        namespace="files",
        config=WorkspaceConfig(
            workspace_backend="r2",
            prefix="agent",
            r2_bucket_name="bucket",
            r2_account_id="account",
            r2_access_key_id="access",
            r2_secret_access_key="secret",
        ),
    )

    assert isinstance(storage, FakeStorage)
    assert captured == {
        "bucket_name": "bucket",
        "account_id": "account",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "prefix": "agent/files",
    }


def test_workspace_storage_requires_r2_configuration():
    try:
        create_workspace_storage(
            namespace="files",
            config=WorkspaceConfig(workspace_backend="r2", r2_bucket_name="bucket"),
        )
    except ValueError as exc:
        message = str(exc)
        assert "R2_ACCOUNT_ID" in message
        assert "R2_ACCESS_KEY_ID" in message
        assert "R2_SECRET_ACCESS_KEY" in message
    else:
        raise AssertionError("R2 workspace storage should require all R2 credentials")


def test_create_workspace_storage_accepts_explicit_workspace_backend(monkeypatch):
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    captured = {}

    class FakeStorage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "omnicoreagent.core.workspace.storage.S3WorkspaceStorage",
        FakeStorage,
    )

    storage = create_workspace_storage(
        namespace="artifacts",
        workspace_backend="s3",
        config=WorkspaceConfig(
            prefix="runtime",
            s3_bucket="bucket",
        ),
    )

    assert isinstance(storage, FakeStorage)
    assert captured["prefix"] == "runtime/artifacts"


def test_ensure_workspace_creates_runtime_directories(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(workspace))

    paths = ensure_workspace()

    assert paths.root.is_dir()
    assert paths.artifacts.is_dir()
    assert paths.files.is_dir()
    assert paths.config.is_dir()


def test_workspace_files_backend_cache_respects_workspace_changes(monkeypatch, tmp_path):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    clear_workspace_files_backend_cache()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(first_workspace))
    first = create_workspace_files_backend()

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(second_workspace))
    second = create_workspace_files_backend()

    assert first is not second
    assert first.base_dir == (first_workspace / "files").resolve()
    assert second.base_dir == (second_workspace / "files").resolve()

    clear_workspace_files_backend_cache()


def test_workspace_files_backend_can_use_existing_workspace(tmp_path):
    workspace = Workspace.from_config(WorkspaceConfig(workspace_dir=tmp_path / "ws"))

    backend = create_workspace_files_backend(workspace=workspace)
    backend.write("scratchpad/progress.md", "done", mode="create")

    saved_file = tmp_path / "ws" / "files" / "scratchpad" / "progress.md"
    assert saved_file.read_text() == "done"


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
    storage = LocalWorkspaceStorage(tmp_path / "workspace" / "files")

    storage.write_text("files/notes/today.md", "note", strip_prefixes=("files",))

    assert storage.read_text("notes/today.md") == "note"
    assert not (tmp_path / "workspace" / "files" / "files").exists()


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


def test_r2_workspace_storage_uses_s3_compatible_storage_without_sse():
    client = FakeS3Client()
    storage = R2WorkspaceStorage(
        bucket_name="bucket",
        account_id="account",
        access_key_id="access",
        secret_access_key="secret",
        prefix="workspace/files",
        client=client,
    )

    storage.write_text("result.txt", "hello")

    assert storage.exists("result.txt")
    assert storage.read_text("result.txt") == "hello"
    assert storage.location("result.txt") == "s3://bucket/workspace/files/result.txt"
    assert client.objects[("bucket", "workspace/files/result.txt")]["Body"] == b"hello"
    assert storage.enable_encryption is False


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
