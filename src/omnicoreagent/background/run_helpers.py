"""Run construction and lifecycle helpers for background execution."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

from omnicoreagent.background.models import (
    BackgroundRun,
    BackgroundTaskSpec,
    BackoffPolicy,
    TriggerType,
    build_session_id,
    build_workspace_path,
    utc_now,
)


def build_run(
    task: BackgroundTaskSpec,
    trigger: TriggerType,
    query: str,
    due_at: datetime | None = None,
    occurrence_id: str | None = None,
) -> BackgroundRun:
    run_id = f"run_{uuid4().hex}"
    return BackgroundRun(
        run_id=run_id,
        task_id=task.task_id,
        agent_id=task.agent_id,
        max_attempts=task.retry_policy.max_retries + 1,
        query_snapshot=query,
        trigger_type=trigger,
        due_at=due_at,
        occurrence_id=occurrence_id,
        session_id=build_session_id(task, run_id),
        workspace_path=build_workspace_path(task, run_id),
    )


def build_run_context(run: BackgroundRun) -> str:
    workspace_root = f"/workspace/{run.workspace_path}"
    return (
        "This is a background run.\n"
        f"run_id: {run.run_id}\n"
        f"task_id: {run.task_id}\n"
        f"workspace_path: {workspace_root}\n"
        "Write durable background output inside this workspace.\n"
        f"- final result: {workspace_root}/output.md\n"
        f"- progress, notes, todos, and resumable work: {workspace_root}/scratchpad/\n"
        f"- logs: {workspace_root}/logs/\n"
        f"- generated artifacts and data files: {workspace_root}/artifacts/\n"
        f"- delegated subagent outputs: {workspace_root}/subagents/\n"
        "Keep your final response concise; put durable detail in workspace files.\n\n"
        f"{run.query_snapshot}"
    )


def is_run_due(run: BackgroundRun) -> bool:
    return run.queued_at is None or run.queued_at <= utc_now()


def run_until_terminal_sleep_seconds(
    run: BackgroundRun,
    deadline: float | None,
    poll_interval_seconds: float,
) -> float:
    interval = poll_interval_seconds
    if run.queued_at is not None:
        delay = (run.queued_at - utc_now()).total_seconds()
        if delay > 0:
            interval = min(interval, delay)
    if deadline is not None:
        remaining = deadline - asyncio.get_running_loop().time()
        interval = min(interval, max(remaining, 0))
    return max(interval, 0.001)


def retry_delay_seconds(task: BackgroundTaskSpec, attempt_number: int) -> int:
    policy = task.retry_policy
    if policy.max_retries <= 0:
        return 0
    if policy.backoff == BackoffPolicy.EXPONENTIAL:
        delay = policy.initial_delay_seconds * (2 ** max(attempt_number - 1, 0))
    else:
        delay = policy.initial_delay_seconds
    return min(delay, policy.max_delay_seconds)


def release_lease_patch() -> dict[str, Any]:
    return {
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
    }


def result_preview(result: Any) -> str:
    if isinstance(result, dict) and "response" in result:
        return str(result["response"])[:1000]
    return str(result)[:1000]
