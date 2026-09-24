#!/usr/bin/env python3
"""One Harbor-shaped trial, end to end: does the local sandbox actually work?

A task directory with failing tests, an agent whose commands run on this
machine in that directory, and a verifier that checks afterwards the way a
harness would: the tests pass, and the agent did not get there by changing
them.

    python trial.py --drive agent     # through agent.run()
    python trial.py --drive cli       # through `omnicoreagent run`

It prints a JSON report and exits non-zero if any check fails, so it can be
run unattended.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTRUCTION = (
    "The tests in this directory fail. Run them, read the failures, fix the "
    "code in receipts.py, and run them again until they all pass."
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _prepare(work: Path) -> Path:
    """A fresh copy of the task, as a harness gives each trial its own."""
    task = work / "task"
    if task.exists():
        shutil.rmtree(task)
    shutil.copytree(HERE / "task", task)
    return task


def _pytest(task: Path) -> tuple[int, str]:
    """The verifier: the task's own tests, run the way a harness runs them."""
    finished = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=task,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return finished.returncode, (finished.stdout + finished.stderr)[-600:]


async def _drive_agent(task: Path, out: Path) -> dict:
    os.environ["TRIAL_TASK_DIR"] = str(task)
    os.environ["TRIAL_OUT"] = str(out)
    sys.path.insert(0, str(HERE))
    from omnicoreagent.cli.agent_file import load_agent

    agent = load_agent(HERE / "agent.py")
    await agent.initialize()
    try:
        result = await agent.run(INSTRUCTION, session_id="harbor-trial")
    finally:
        await agent.cleanup()
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "trace_id": result.get("trace_id"),
    }


def _drive_cli(task: Path, out: Path) -> dict:
    """The path a harness takes: one process, one instruction, files on disk."""
    finished = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnicoreagent.cli",
            "run",
            "--agent",
            str(HERE / "agent.py"),
            "-i",
            INSTRUCTION,
            "--approval-mode",
            "deny",
            "--timeout",
            "600",
            "--provenance",
            "adapter=harbor",
            "--provenance",
            "trial_id=receipts-1",
            "-o",
            str(out),
        ],
        cwd=task,
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "TRIAL_TASK_DIR": str(task), "TRIAL_OUT": str(out)},
    )
    document = {}
    result_file = out / "result.json"
    if result_file.exists():
        document = json.loads(result_file.read_text())
    return {
        "exit_code": finished.returncode,
        "status": document.get("status"),
        "run_id": document.get("run_id"),
        "trace_ids": document.get("trace_ids"),
        "stderr_tail": finished.stderr[-400:],
        "trajectory": (out / "trajectory.json").exists(),
    }


def _evidence(out: Path) -> dict:
    """What the trace says the run did, read the way a harness would."""
    commands, surfaces, workspace_files = 0, set(), []
    log = out / "traces.jsonl"
    if not log.exists():
        return {"commands": 0, "surfaces": [], "workspace_files": [], "trace": "missing"}
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = (json.loads(line).get("payload") or {})
        except json.JSONDecodeError:
            continue
        if payload.get("event_type") != "tool_result":
            continue
        data = (payload.get("output") or {}).get("data") or {}
        if "execution_surface" not in data:
            continue
        commands += 1
        surfaces.add(data["execution_surface"])
        workspace_files.extend((data.get("workspace_files") or {}).get("written") or [])
    return {
        "commands": commands,
        "surfaces": sorted(surfaces),
        "workspace_files": sorted(set(workspace_files)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drive", choices=["agent", "cli"], default="agent")
    parser.add_argument("--work", default="/trial/work")
    arguments = parser.parse_args()

    work = Path(arguments.work)
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)
    task = _prepare(work)
    tests_before = _digest(task / "test_receipts.py")
    before_code, before_output = _pytest(task)

    if arguments.drive == "agent":
        run = asyncio.run(_drive_agent(task, out))
    else:
        run = _drive_cli(task, out)

    after_code, after_output = _pytest(task)
    tests_after = _digest(task / "test_receipts.py")
    # Running the tests leaves pytest's own cache; that is the task's doing.
    left_behind = sorted(
        item.name
        for item in task.iterdir()
        if item.name
        not in {"receipts.py", "test_receipts.py", "__pycache__", ".pytest_cache"}
    )
    evidence = _evidence(out)

    checks = {
        "the tests failed before": before_code != 0,
        "the tests pass after": after_code == 0,
        "the tests were not changed": tests_before == tests_after,
        "nothing was left in the task directory": not left_behind,
        "the run finished": run.get("status") in {"success", "completed"},
    }
    checks.update(
        {
            "every command ran on the host surface": evidence["surfaces"] == ["host"],
            "the agent ran commands": evidence["commands"] > 0,
            "the workspace was not copied in or out": not evidence["workspace_files"],
        }
    )
    report = {
        "drive": arguments.drive,
        "run": run,
        "evidence": evidence,
        "before": before_output.strip().splitlines()[-1:],
        "after": after_output.strip().splitlines()[-1:],
        "left_behind": left_behind,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if report["failed_checks"] else 0)


if __name__ == "__main__":
    main()
