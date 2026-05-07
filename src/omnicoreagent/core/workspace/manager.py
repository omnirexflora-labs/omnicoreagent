from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from omnicoreagent.core.workspace.config import (
    WorkspaceConfig,
    resolve_workspace_config,
)
from omnicoreagent.core.workspace.paths import (
    WORKSPACE_NAMESPACE_ARTIFACTS,
    WORKSPACE_NAMESPACE_FILES,
)
from omnicoreagent.core.workspace.storage import (
    WorkspaceStorage,
    create_workspace_storage,
)


@dataclass(slots=True)
class Workspace:
    """Runtime workspace facade with stable files and artifacts areas."""

    config: WorkspaceConfig
    files: WorkspaceStorage
    artifacts: WorkspaceStorage

    @classmethod
    def from_config(
        cls,
        config: WorkspaceConfig | dict | None = None,
        *,
        workspace_dir: str | Path | None = None,
        workspace_backend: str | None = None,
    ) -> "Workspace":
        workspace_config = resolve_workspace_config(config).with_overrides(
            workspace_backend=workspace_backend,
            workspace_dir=workspace_dir,
        )
        return cls(
            config=workspace_config,
            files=create_workspace_storage(
                namespace=WORKSPACE_NAMESPACE_FILES,
                config=workspace_config,
            ),
            artifacts=create_workspace_storage(
                namespace=WORKSPACE_NAMESPACE_ARTIFACTS,
                config=workspace_config,
            ),
        )

    def ensure(self) -> "Workspace":
        self.files.ensure_root()
        self.artifacts.ensure_root()
        return self
