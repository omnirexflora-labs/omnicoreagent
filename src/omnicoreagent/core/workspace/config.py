import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping


DEFAULT_WORKSPACE_BACKEND = "local"
DEFAULT_WORKSPACE_DIR = "./workspace"
DEFAULT_WORKSPACE_PREFIX = "workspace"


@dataclass(frozen=True)
class WorkspaceConfig:
    """Explicit workspace storage configuration."""

    backend: str = DEFAULT_WORKSPACE_BACKEND
    workspace_dir: str | Path | None = DEFAULT_WORKSPACE_DIR
    prefix: str = DEFAULT_WORKSPACE_PREFIX
    s3_bucket: str | None = None
    aws_region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_endpoint_url: str | None = None
    r2_bucket_name: str | None = None
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", self.backend.lower().strip())
        object.__setattr__(self, "prefix", self.prefix.strip("/"))
        if self.workspace_dir is not None:
            object.__setattr__(self, "workspace_dir", str(self.workspace_dir))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkspaceConfig":
        source = env if env is not None else os.environ
        return cls(
            backend=source.get(
                "OMNICOREAGENT_WORKSPACE_BACKEND", DEFAULT_WORKSPACE_BACKEND
            ),
            workspace_dir=source.get(
                "OMNICOREAGENT_WORKSPACE_DIR", DEFAULT_WORKSPACE_DIR
            ),
            prefix=source.get(
                "OMNICOREAGENT_WORKSPACE_PREFIX", DEFAULT_WORKSPACE_PREFIX
            ),
            s3_bucket=source.get("AWS_S3_BUCKET"),
            aws_region=source.get("AWS_REGION"),
            aws_access_key_id=source.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=source.get("AWS_SECRET_ACCESS_KEY"),
            aws_endpoint_url=source.get("AWS_ENDPOINT_URL"),
            r2_bucket_name=source.get("R2_BUCKET_NAME"),
            r2_account_id=source.get("R2_ACCOUNT_ID"),
            r2_access_key_id=source.get("R2_ACCESS_KEY_ID"),
            r2_secret_access_key=source.get("R2_SECRET_ACCESS_KEY"),
        )

    def with_overrides(
        self,
        *,
        backend: str | None = None,
        workspace_dir: str | Path | None = None,
    ) -> "WorkspaceConfig":
        updates = {}
        if backend is not None:
            updates["backend"] = backend
        if workspace_dir is not None:
            updates["workspace_dir"] = workspace_dir
        return replace(self, **updates)

    def namespace_prefix(self, namespace: str | None = None) -> str:
        clean_namespace = (namespace or "").strip("/")
        if self.prefix and clean_namespace:
            return f"{self.prefix}/{clean_namespace}"
        return clean_namespace or self.prefix

    def local_namespace_path(self, namespace: str | None = None) -> Path:
        root = Path(self.workspace_dir or DEFAULT_WORKSPACE_DIR)
        clean_namespace = (namespace or "").strip("/")
        return root / clean_namespace if clean_namespace else root

    def cache_key(self, namespace: str | None = None) -> tuple:
        if self.backend == "local":
            return (
                self.backend,
                str(self.local_namespace_path(namespace).expanduser().resolve()),
            )

        if self.backend == "s3":
            return (
                self.backend,
                self.s3_bucket,
                self.aws_region,
                self.aws_endpoint_url,
                self.namespace_prefix(namespace),
            )

        if self.backend == "r2":
            return (
                self.backend,
                self.r2_bucket_name,
                self.r2_account_id,
                self.namespace_prefix(namespace),
            )

        return (self.backend,)


def resolve_workspace_config(
    config: WorkspaceConfig | Mapping[str, object] | None = None,
) -> WorkspaceConfig:
    if config is None:
        return WorkspaceConfig.from_env()
    if isinstance(config, WorkspaceConfig):
        return config
    return WorkspaceConfig(**config)
