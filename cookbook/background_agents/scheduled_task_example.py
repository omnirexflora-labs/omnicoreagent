#!/usr/bin/env python3
"""Run a scheduled background task without requiring an LLM API key.

Run:
    python3 cookbook/background_agents/scheduled_task_example.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from typing import Any

from _bootstrap import ROOT_DIR  # noqa: F401
from omnicoreagent import BackgroundAgentManager
from omnicoreagent.background import RunStatus
from omnicoreagent.core.workspace.manager import Workspace
from omnicoreagent.background.models import TERMINAL_RUN_STATUSES


class ScheduledReportAgent:
    """Small deterministic agent used to demonstrate background scheduling."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.calls: list[dict[str, Any]] = []

    async def run(self, query: str, session_id: str | None = None) -> dict[str, str]:
        self.calls.append({"query": query, "session_id": session_id})
        workspace_path = workspace_path_from_query(query)
        self.workspace.files.write_text(
            f"{workspace_path}/output.md",
            "Scheduled report\n\n- status: complete\n- owner: background worker\n",
        )
        return {
            "response": (
                "scheduled report complete; "
                f"session={session_id}; "
                f"workspace_guidance={'/workspace/background/' in query}"
            )
        }


def workspace_path_from_query(query: str) -> str:
    for line in query.splitlines():
        if line.startswith("workspace_path: /workspace/"):
            return line.removeprefix("workspace_path: /workspace/")
    raise ValueError("background run query did not include workspace_path guidance")


async def wait_for_terminal_run(
    manager: BackgroundAgentManager,
    task_id: str,
    *,
    timeout_seconds: float = 3.0,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        runs = await manager.list_runs(task_id=task_id)
        terminal = [run for run in runs if run.status in TERMINAL_RUN_STATUSES]
        if terminal:
            latest = terminal[-1]
            if latest.status != RunStatus.COMPLETED:
                raise RuntimeError(
                    f"background task ended with {latest.status.value}: {latest.error}"
                )
            return latest
        await asyncio.sleep(0.05)
    raise TimeoutError(f"background task did not complete: {task_id}")


async def run_scheduled_example(workspace_dir: str | Path | None = None) -> dict[str, Any]:
    workspace_root = Path(
        workspace_dir
        or os.getenv(
            "OMNICOREAGENT_COOKBOOK_WORKSPACE_DIR",
            str(Path(tempfile.gettempdir()) / "omnicoreagent_cookbook_background_workspace"),
        )
    )
    workspace = Workspace.from_config(workspace_dir=workspace_root).ensure()
    agent = ScheduledReportAgent(workspace)
    manager = BackgroundAgentManager(workspace=workspace, worker_id="cookbook_worker")

    result: dict[str, Any] | None = None
    try:
        await manager.register_agent("scheduled_reporter", agent)
        await manager.register_task(
            task_id="daily_report",
            agent_id="scheduled_reporter",
            query="Write the scheduled operational report.",
            schedule={
                "type": "once",
                "run_at": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
            timeout_seconds=10,
            retry_policy={"max_retries": 1, "initial_delay_seconds": 0},
            overlap_policy="queue_next",
        )

        await manager.start()
        run = await wait_for_terminal_run(manager, "daily_report")
        events = await manager.get_run_events(run.run_id)
        task_status = await manager.get_task_status("daily_report")
        manager_status = await manager.get_manager_status()
        workspace_state = await manager.get_run_workspace(run.run_id)

        result = {
            "run_id": run.run_id,
            "status": run.status.value,
            "result_preview": run.result_preview,
            "task_status": task_status,
            "manager_status": manager_status,
            "events": [event["event"] for event in events],
            "workspace_path": run.workspace_path,
            "workspace_files": [item["name"] for item in workspace_state["files"]],
            "agent_calls": agent.calls,
        }
    finally:
        await manager.shutdown()
    result["shutdown_status"] = await manager.get_manager_status()
    return result


async def main() -> None:
    result = await run_scheduled_example()
    print(f"run_id={result['run_id']}")
    print(f"status={result['status']}")
    print(f"runs={result['task_status']['runs']}")
    print(f"active_runs={result['task_status']['active_runs']}")
    print(f"completed={result['task_status']['status_counts']['completed']}")
    print(f"manager_worker={result['manager_status']['worker_id']}")
    print(f"manager_runs={result['manager_status']['runs']}")
    print(f"manager_active_runs={result['manager_status']['active_runs']}")
    print(f"workspace={result['workspace_path']}")
    print(f"files={','.join(result['workspace_files'])}")
    print(f"events={','.join(result['events'])}")
    print(result["result_preview"])


if __name__ == "__main__":
    asyncio.run(main())
