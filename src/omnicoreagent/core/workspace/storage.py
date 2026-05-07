import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Protocol

from filelock import FileLock

from omnicoreagent._optional import load_optional
from omnicoreagent.core.workspace.config import (
    WorkspaceConfig,
    resolve_workspace_config,
)
from omnicoreagent.core.workspace.paths import normalize_workspace_path


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    name: str
    modified_at: datetime
    is_dir: bool = False


class WorkspaceStorage(Protocol):
    def ensure_root(self) -> None: ...

    def read_text(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        ...

    def exists(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> bool:
        ...

    def location(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        ...

    def list_files(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> list[WorkspaceFile]: ...

    def write_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
        atomic: bool = True,
    ) -> Any: ...

    def append_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Any: ...

    def delete(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        ...

    def rename(
        self,
        old_path: str | Path,
        new_path: str | Path,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Any: ...

    def clear(self) -> None: ...


class LocalWorkspaceStorage:
    """Safe local storage rooted inside one workspace namespace."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.ensure_root()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Path:
        self.ensure_root()
        if path is None or str(path).strip() == "":
            return self.root

        relative_path = normalize_workspace_path(path, strip_prefixes=strip_prefixes)
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError(
                f"Invalid path '{path}' resolved outside workspace namespace.\n"
                f"Path traversal detected.\nAll paths must stay inside: {self.root}"
            )
        return candidate

    def describe_root(self) -> str:
        contents = self.list_files()
        if not contents:
            return "(empty)"
        return "\n".join(
            f"{item.name}/" if item.is_dir else item.name for item in contents
        )

    def read_text(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        with FileLock(resolved.with_suffix(".lock")):
            return resolved.read_text(encoding="utf-8")

    def exists(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> bool:
        return self.resolve(path, strip_prefixes=strip_prefixes).exists()

    def location(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        return str(self.resolve(path, strip_prefixes=strip_prefixes))

    def list_files(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> list[WorkspaceFile]:
        target = self.resolve(path, strip_prefixes=strip_prefixes)
        if not target.exists() or not target.is_dir():
            return []

        files = []
        for child in target.iterdir():
            files.append(
                WorkspaceFile(
                    path=str(child.relative_to(self.root)),
                    name=child.name,
                    modified_at=datetime.fromtimestamp(child.stat().st_mtime),
                    is_dir=child.is_dir(),
                )
            )
        return files

    def write_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
        atomic: bool = True,
    ) -> Path:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if not atomic:
            resolved.write_text(content, encoding="utf-8")
            return resolved

        with FileLock(resolved.with_suffix(".lock")):
            tmp_path = resolved.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(resolved)
        return resolved

    def append_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Path:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(resolved.with_suffix(".lock")):
            if resolved.exists():
                existing = resolved.read_text(encoding="utf-8")
                content = existing.rstrip("\n") + "\n" + content
            tmp_path = resolved.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(resolved)
        return resolved

    def delete(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        with FileLock(resolved.with_suffix(".lock")):
            if resolved.is_file():
                resolved.unlink()
                return f"File deleted: {resolved}"
            if resolved.is_dir():
                shutil.rmtree(resolved)
                return f"Directory deleted: {resolved}"
            return f"Path not found: {path}"

    def rename(
        self,
        old_path: str | Path,
        new_path: str | Path,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> tuple[Path, Path]:
        old_resolved = self.resolve(old_path, strip_prefixes=strip_prefixes)
        new_resolved = self.resolve(new_path, strip_prefixes=strip_prefixes)
        if not old_resolved.exists():
            raise FileNotFoundError(str(old_path))

        new_resolved.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(old_resolved.with_suffix(".lock")), FileLock(
            new_resolved.with_suffix(".lock")
        ):
            old_resolved.rename(new_resolved)
        return old_resolved, new_resolved

    def clear(self) -> None:
        self.ensure_root()
        with FileLock(self.root.with_suffix(".lock")):
            for item in list(self.root.iterdir()):
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)


class S3WorkspaceStorage:
    """S3-compatible workspace storage rooted at a key prefix."""

    def __init__(
        self,
        *,
        bucket_name: str,
        prefix: str = "workspace/",
        region: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        endpoint_url: str | None = None,
        enable_encryption: bool = True,
        client: Any = None,
    ):
        self.bucket = bucket_name
        self.prefix = prefix.strip("/")
        self.prefix = f"{self.prefix}/" if self.prefix else ""
        self.enable_encryption = enable_encryption

        if client is not None:
            self.s3 = client
            return

        boto3 = load_optional("S3 workspace storage", "s3", lambda: __import__("boto3"))
        Config = load_optional(
            "S3 workspace storage",
            "s3",
            lambda: __import__("botocore.config", fromlist=["Config"]).Config,
        )
        client_params = {
            "service_name": "s3",
            "config": Config(region_name=region),
        }
        if endpoint_url:
            client_params["endpoint_url"] = endpoint_url
        if aws_access_key_id and aws_secret_access_key:
            client_params["aws_access_key_id"] = aws_access_key_id
            client_params["aws_secret_access_key"] = aws_secret_access_key
        self.s3 = boto3.client(**client_params)

    def ensure_root(self) -> None:
        return None

    def _normalize_key(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> str:
        if path is None or str(path).strip() == "":
            return self.prefix

        relative_path = normalize_workspace_path(path, strip_prefixes=strip_prefixes)
        return f"{self.prefix}{relative_path}" if relative_path else self.prefix

    def _put_params(self) -> dict[str, Any]:
        if not self.enable_encryption:
            return {}
        return {"ServerSideEncryption": "AES256"}

    def read_text(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        key = self._normalize_key(path, strip_prefixes=strip_prefixes)
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read().decode("utf-8")

    def exists(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> bool:
        key = self._normalize_key(path, strip_prefixes=strip_prefixes)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def location(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        key = self._normalize_key(path, strip_prefixes=strip_prefixes)
        return f"s3://{self.bucket}/{key}"

    def list_files(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> list[WorkspaceFile]:
        prefix = self._normalize_key(path, strip_prefixes=strip_prefixes)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        response = self.s3.list_objects_v2(
            Bucket=self.bucket,
            Prefix=prefix,
            Delimiter="/",
        )
        files = []
        for item in response.get("CommonPrefixes", []):
            key = item["Prefix"]
            name = key[len(prefix) :].rstrip("/")
            if not name:
                continue
            files.append(
                WorkspaceFile(
                    path=key[len(self.prefix) :].rstrip("/"),
                    name=name,
                    modified_at=datetime.fromtimestamp(0),
                    is_dir=True,
                )
            )
        for item in response.get("Contents", []):
            key = item["Key"]
            if key == prefix or key.endswith("/"):
                continue
            name = key[len(prefix) :]
            if "/" in name:
                continue
            files.append(
                WorkspaceFile(
                    path=key[len(self.prefix) :],
                    name=name,
                    modified_at=item["LastModified"],
                )
            )
        return files

    def write_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
        atomic: bool = True,
    ) -> str:
        key = self._normalize_key(path, strip_prefixes=strip_prefixes)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content.encode("utf-8"),
            **self._put_params(),
        )
        return key

    def append_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> str:
        if self.exists(path, strip_prefixes=strip_prefixes):
            content = (
                self.read_text(path, strip_prefixes=strip_prefixes).rstrip("\n")
                + "\n"
                + content
            )
        return self.write_text(path, content, strip_prefixes=strip_prefixes)

    def delete(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        key = self._normalize_key(path, strip_prefixes=strip_prefixes)
        if self.exists(path, strip_prefixes=strip_prefixes):
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            return f"File deleted: s3://{self.bucket}/{key}"

        prefix = key if key.endswith("/") else f"{key}/"
        response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
        if objects:
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
            return f"Directory deleted: s3://{self.bucket}/{prefix}"

        self.s3.delete_object(Bucket=self.bucket, Key=key)
        return f"File deleted: s3://{self.bucket}/{key}"

    def rename(
        self,
        old_path: str | Path,
        new_path: str | Path,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> tuple[str, str]:
        old_key = self._normalize_key(old_path, strip_prefixes=strip_prefixes)
        new_key = self._normalize_key(new_path, strip_prefixes=strip_prefixes)
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": old_key},
            Key=new_key,
            **self._put_params(),
        )
        self.s3.delete_object(Bucket=self.bucket, Key=old_key)
        return old_key, new_key

    def clear(self) -> None:
        response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix)
        objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
        if objects:
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})


class R2WorkspaceStorage(S3WorkspaceStorage):
    def __init__(
        self,
        *,
        bucket_name: str,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        prefix: str = "workspace/",
        client: Any = None,
    ):
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        super().__init__(
            bucket_name=bucket_name,
            prefix=prefix,
            region="auto",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url,
            enable_encryption=False,
            client=client,
        )


def create_workspace_storage(
    *,
    backend: str | None = None,
    namespace: str | None = None,
    workspace_dir: str | Path | None = None,
    config: WorkspaceConfig | dict | None = None,
) -> WorkspaceStorage:
    workspace_config = resolve_workspace_config(config)
    workspace_config = workspace_config.with_overrides(
        backend=backend,
        workspace_dir=workspace_dir,
    )
    backend = workspace_config.backend
    namespace = (namespace or "").strip("/")

    if backend == "local":
        root = workspace_config.local_namespace_path(namespace)
        return LocalWorkspaceStorage(root)

    if backend == "s3":
        bucket_name = workspace_config.s3_bucket
        if not bucket_name:
            raise ValueError("S3 workspace backend requires AWS_S3_BUCKET")
        prefix = workspace_config.namespace_prefix(namespace)
        return S3WorkspaceStorage(
            bucket_name=bucket_name,
            prefix=prefix,
            region=workspace_config.aws_region,
            aws_access_key_id=workspace_config.aws_access_key_id,
            aws_secret_access_key=workspace_config.aws_secret_access_key,
            endpoint_url=workspace_config.aws_endpoint_url,
        )

    if backend == "r2":
        required_fields = {
            "R2_BUCKET_NAME": workspace_config.r2_bucket_name,
            "R2_ACCOUNT_ID": workspace_config.r2_account_id,
            "R2_ACCESS_KEY_ID": workspace_config.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": workspace_config.r2_secret_access_key,
        }
        missing = [name for name, value in required_fields.items() if not value]
        if missing:
            raise ValueError(
                f"R2 workspace backend requires environment variables: {', '.join(missing)}"
            )
        prefix = workspace_config.namespace_prefix(namespace)
        return R2WorkspaceStorage(
            bucket_name=workspace_config.r2_bucket_name,
            account_id=workspace_config.r2_account_id,
            access_key_id=workspace_config.r2_access_key_id,
            secret_access_key=workspace_config.r2_secret_access_key,
            prefix=prefix,
        )

    raise ValueError("workspace backend must be one of: local, s3, r2")
