from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXAMPLE_PATH = ROOT / "cookbook" / "background_agents" / "scheduled_task_example.py"
REAL_APP_EXAMPLE_PATH = (
    ROOT / "cookbook" / "background_agents" / "real_application_background_task.py"
)


def load_example_module(path: Path, module_name: str):
    script_dir = str(path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_scheduled_example_module():
    return load_example_module(EXAMPLE_PATH, "scheduled_task_example")


def load_real_app_background_module():
    return load_example_module(
        REAL_APP_EXAMPLE_PATH,
        "real_application_background_task",
    )


def test_real_application_background_cookbook_imports_as_package():
    from cookbook.background_agents import real_application_background_task

    assert hasattr(
        real_application_background_task,
        "run_real_application_background_example",
    )


@pytest.mark.asyncio
async def test_background_scheduled_cookbook_example_runs_end_to_end(tmp_path):
    module = load_scheduled_example_module()

    result = await module.run_scheduled_example(tmp_path)

    assert result["status"] == "completed"
    assert result["task_status"]["runs"] == 1
    assert result["task_status"]["active_runs"] == 0
    assert result["task_status"]["status_counts"]["completed"] == 1
    assert result["manager_status"]["worker_id"] == "cookbook_worker"
    assert result["manager_status"]["runs"] == 1
    assert result["shutdown_status"]["running"] is False
    assert result["shutdown_status"]["initialized"] is False
    assert result["shutdown_status"]["runs"] == 1
    assert "background_task_scheduled" in result["events"]
    assert "background_run_completed" in result["events"]
    assert {"output.md", "run.json", "events.jsonl"}.issubset(
        set(result["workspace_files"])
    )
    assert (
        result["agent_calls"][0]["session_id"]
        == "background:scheduled_reporter:daily_report"
    )
    assert "/workspace/background/" in result["agent_calls"][0]["query"]


def test_background_scheduled_cookbook_script_runs_as_plain_script(tmp_path):
    env = os.environ.copy()
    env["OMNICOREAGENT_COOKBOOK_WORKSPACE_DIR"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, str(EXAMPLE_PATH)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "status=completed" in result.stdout
    assert "completed=1" in result.stdout
    assert "manager_worker=cookbook_worker" in result.stdout
    assert "manager_runs=1" in result.stdout
    assert "manager_active_runs=0" in result.stdout
    assert "background_task_scheduled" in result.stdout
    assert "background_run_completed" in result.stdout
    assert "output.md" in result.stdout
    assert "run.json" in result.stdout
    assert "events.jsonl" in result.stdout


@pytest.mark.asyncio
async def test_real_application_background_cookbook_example_runs_end_to_end(tmp_path):
    module = load_real_app_background_module()

    result = await module.run_real_application_background_example(tmp_path)

    assert result["status"] == "completed"
    assert result["task_status"]["runs"] == 1
    assert result["task_status"]["active_runs"] == 0
    assert result["task_status"]["status_counts"]["completed"] == 1
    assert result["manager_status"]["worker_id"] == "real_app_worker"
    assert result["manager_status"]["runs"] == 1
    assert result["workspace_root"] == str(tmp_path)
    assert result["shutdown_status"]["running"] is False
    assert result["shutdown_status"]["initialized"] is False
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["status"] == "completed"
    assert "background_run_queued" in result["events"]
    assert "background_run_completed" in result["events"]
    assert {"tickets", "output.md", "run.json", "events.jsonl"}.issubset(
        set(result["workspace_files"])
    )
    assert (
        result["agent_calls"][0]["session_id"]
        == "background:support_ops:support_ticket_tck_1042"
    )
    assert result["agent_calls"][0]["run_id"] == result["run_id"]
    assert "/workspace/background/" in result["agent_calls"][0]["query"]
    assert "support background task complete" in result["result_preview"]


def test_real_application_background_cookbook_script_runs_as_plain_script(tmp_path):
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, str(REAL_APP_EXAMPLE_PATH)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "status=completed" in result.stdout
    assert "completed=1" in result.stdout
    assert "manager_worker=real_app_worker" in result.stdout
    assert f"workspace_root={tmp_path}" in result.stdout
    assert "background_run_queued" in result.stdout
    assert "background_run_completed" in result.stdout
    assert "tickets" in result.stdout
    assert "output.md" in result.stdout
    assert "run.json" in result.stdout
    assert "events.jsonl" in result.stdout
