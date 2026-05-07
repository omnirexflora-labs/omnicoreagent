from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from omnicoreagent.core.workspace.config import (
    DEFAULT_WORKSPACE_DIR,
    WorkspaceConfig,
    resolve_workspace_config,
)
from omnicoreagent.core.workspace.paths import (
    WORKSPACE_NAMESPACE_ARTIFACTS,
    WORKSPACE_NAMESPACE_CONFIG,
    WORKSPACE_NAMESPACE_FILES,
)

if TYPE_CHECKING:
    from omnicoreagent.core.workspace.manager import Workspace


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    artifacts: Path
    files: Path
    config: Path

    def ensure(self) -> "WorkspacePaths":
        for path in (self.root, self.artifacts, self.files, self.config):
            path.mkdir(parents=True, exist_ok=True)
        return self


def resolve_workspace_paths(
    *,
    workspace_dir: str | Path | None = None,
    config: WorkspaceConfig | dict | None = None,
) -> WorkspacePaths:
    workspace_config = resolve_workspace_config(config)
    workspace_config = workspace_config.with_overrides(workspace_dir=workspace_dir)
    root = Path(workspace_config.workspace_dir or DEFAULT_WORKSPACE_DIR)
    return WorkspacePaths(
        root=root,
        artifacts=workspace_config.local_namespace_path(WORKSPACE_NAMESPACE_ARTIFACTS),
        files=workspace_config.local_namespace_path(WORKSPACE_NAMESPACE_FILES),
        config=workspace_config.local_namespace_path(WORKSPACE_NAMESPACE_CONFIG),
    )


def get_workspace_dir() -> str:
    return str(resolve_workspace_paths().root)


def get_artifacts_dir() -> str:
    return str(resolve_workspace_paths().artifacts)


def get_workspace_files_dir() -> str:
    return str(resolve_workspace_paths().files)


def get_config_dir() -> str:
    return str(resolve_workspace_paths().config)


def ensure_workspace(
    *,
    workspace_dir: str | Path | None = None,
    config: WorkspaceConfig | dict | None = None,
) -> WorkspacePaths:
    return resolve_workspace_paths(workspace_dir=workspace_dir, config=config).ensure()


def __getattr__(name: str):
    if name == "Workspace":
        from omnicoreagent.core.workspace.manager import Workspace

        return Workspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Workspace",
    "WorkspacePaths",
    "ensure_workspace",
    "get_artifacts_dir",
    "get_config_dir",
    "get_workspace_dir",
    "get_workspace_files_dir",
    "resolve_workspace_paths",
]
