"""Workspace IO boundary for background execution."""

from __future__ import annotations

import json
from typing import Any

from omnicoreagent.background.errors import RunNotFoundError
from omnicoreagent.background.models import BackgroundRun


class BackgroundWorkspaceIO:
    """Small adapter around workspace storage used by background runs."""

    def __init__(self, workspace: Any = None) -> None:
        self._workspace = workspace

    def resolve(self) -> Any | None:
        if self._workspace is not None:
            return self._workspace
        try:
            from omnicoreagent.core.workspace.manager import Workspace

            self._workspace = Workspace.from_config().ensure()
        except Exception:
            self._workspace = None
        return self._workspace

    def run_files(self, run: BackgroundRun | None) -> dict[str, Any]:
        if run is None:
            raise RunNotFoundError("Run not found")

        workspace = self.resolve()
        files = []
        if workspace is not None:
            try:
                workspace_files = workspace.files.list_files(run.workspace_path)
            except Exception:
                workspace_files = []
            for item in workspace_files:
                files.append(
                    {
                        "path": item.path,
                        "name": item.name,
                        "modified_at": item.modified_at.isoformat(),
                        "is_dir": item.is_dir,
                    }
                )

        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "workspace_path": run.workspace_path,
            "files": files,
        }

    def read_events(self, workspace_path: str) -> list[dict[str, Any]]:
        workspace = self.resolve()
        if workspace is None:
            return []
        try:
            content = workspace.files.read_text(f"{workspace_path}/events.jsonl")
        except Exception:
            return []
        events: list[dict[str, Any]] = []
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def write_run_snapshot(self, run: BackgroundRun) -> None:
        workspace = self.resolve()
        if workspace is None:
            return
        workspace.files.write_text(
            f"{run.workspace_path}/run.json",
            run.model_dump_json(indent=2),
        )

    def append_event(self, event: dict[str, Any]) -> None:
        workspace_path = event.get("workspace_path")
        if not workspace_path:
            return
        workspace = self.resolve()
        if workspace is None:
            return
        workspace.files.append_text(
            f"{workspace_path}/events.jsonl",
            json.dumps(event, default=str),
        )
