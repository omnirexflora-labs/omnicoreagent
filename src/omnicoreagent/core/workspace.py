import os
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_WORKSPACE = "./workspace"
_WORKSPACE_ENV = "OMNICOREAGENT_WORKSPACE_DIR"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    artifacts: Path
    memories: Path
    config: Path

    def ensure(self) -> "WorkspacePaths":
        for path in (self.root, self.artifacts, self.memories, self.config):
            path.mkdir(parents=True, exist_ok=True)
        return self


def resolve_workspace_paths(
    *,
    workspace_dir: str | os.PathLike | None = None,
) -> WorkspacePaths:
    root = Path(workspace_dir or os.environ.get(_WORKSPACE_ENV, _DEFAULT_WORKSPACE))
    return WorkspacePaths(
        root=root,
        artifacts=root / "artifacts",
        memories=root / "memories",
        config=root / "config",
    )


def get_workspace_dir() -> str:
    return str(resolve_workspace_paths().root)


def get_artifacts_dir() -> str:
    return str(resolve_workspace_paths().artifacts)


def get_memories_dir() -> str:
    return str(resolve_workspace_paths().memories)


def get_config_dir() -> str:
    return str(resolve_workspace_paths().config)


def ensure_workspace() -> WorkspacePaths:
    return resolve_workspace_paths().ensure()
