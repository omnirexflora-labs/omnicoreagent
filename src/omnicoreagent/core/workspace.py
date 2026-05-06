from dataclasses import dataclass
from pathlib import Path

from omnicoreagent.core.workspace_config import DEFAULT_WORKSPACE_DIR, WorkspaceConfig

_DEFAULT_WORKSPACE = DEFAULT_WORKSPACE_DIR


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
) -> WorkspacePaths:
    root = Path(workspace_dir or WorkspaceConfig.from_env().workspace_dir)
    return WorkspacePaths(
        root=root,
        artifacts=root / "artifacts",
        files=root / "files",
        config=root / "config",
    )


def get_workspace_dir() -> str:
    return str(resolve_workspace_paths().root)


def get_artifacts_dir() -> str:
    return str(resolve_workspace_paths().artifacts)


def get_workspace_files_dir() -> str:
    return str(resolve_workspace_paths().files)


def get_config_dir() -> str:
    return str(resolve_workspace_paths().config)


def ensure_workspace() -> WorkspacePaths:
    return resolve_workspace_paths().ensure()
