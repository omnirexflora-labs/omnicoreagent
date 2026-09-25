"""What a Harbor job did, read from its own files.

    omnicoreagent harbor results jobs/2026-09-25__06-07-24
    omnicoreagent harbor results jobs --json

Harbor's summary command is a removed shim, and a job whose every trial errored
still exits 0, so this answers the question a person running trials asks first:
which passed, which failed, which never got to run — and why — with what it
cost, without opening a log. Trials are read with Harbor's own result model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import click

_ERROR_LINE = re.compile(r"error|Error|ERROR|Traceback|No module|not found|denied", re.ASCII)


def _is_job(path: Path) -> bool:
    return (path / "config.json").is_file() and any(
        (child / "config.json").is_file() for child in path.iterdir() if child.is_dir()
    )


def _error_line(exception_type: str, message: str) -> str:
    """The line of an exception that says what went wrong.

    Harbor's message for a failed command starts with the whole command; the
    reason is further down, in what the command printed.
    """
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    rest = lines[1:] if lines and lines[0].startswith("Command failed") else lines
    for line in rest:
        line = re.sub(r"^(stdout|stderr):\s*", "", line)
        if _ERROR_LINE.search(line):
            return f"{exception_type}: {line[:240]}"
    return f"{exception_type}: {(lines[0] if lines else '')[:240]}"


def _reward(verifier_result: Any) -> float | None:
    rewards = getattr(verifier_result, "rewards", None) or {}
    if "reward" in rewards:
        return float(rewards["reward"])
    for value in rewards.values():
        return float(value)
    return None


def _steps(trajectory: Path) -> int | None:
    try:
        document = json.loads(trajectory.read_text())
    except (OSError, ValueError):
        return None
    return (document.get("final_metrics") or {}).get("total_steps") or len(
        document.get("steps") or []
    ) or None


def summarize_trial(trial_dir: Path) -> dict[str, Any]:
    from harbor.models.trial.result import TrialResult

    entry: dict[str, Any] = {
        "trial": trial_dir.name,
        "task": None,
        "outcome": "running",
        "reward": None,
        "status": None,
        "termination_reason": None,
        "detail": None,
        "exit_code": None,
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_tokens": None,
        "steps": None,
        "duration_s": None,
        "error": None,
        "mcp_failed": [],
        "note": None,
        "trajectory": None,
    }
    result_path = trial_dir / "result.json"
    if not result_path.is_file():
        return entry
    result = TrialResult.model_validate_json(result_path.read_text())
    agent = result.agent_result
    metadata = (agent.metadata if agent else None) or {}
    trajectory = trial_dir / "agent" / "trajectory.json"
    entry.update(
        task=result.task_name,
        reward=_reward(result.verifier_result),
        status=metadata.get("omnicoreagent_status"),
        termination_reason=metadata.get("omnicoreagent_termination_reason"),
        detail=metadata.get("omnicoreagent_detail"),
        exit_code=metadata.get("omnicoreagent_exit_code"),
        cost_usd=agent.cost_usd if agent else None,
        input_tokens=agent.n_input_tokens if agent else None,
        output_tokens=agent.n_output_tokens if agent else None,
        cache_tokens=agent.n_cache_tokens if agent else None,
        mcp_failed=list(metadata.get("omnicoreagent_mcp_failed") or []),
        trajectory=str(trajectory) if trajectory.is_file() else None,
        steps=_steps(trajectory) if trajectory.is_file() else None,
    )
    if result.started_at and result.finished_at:
        entry["duration_s"] = round((result.finished_at - result.started_at).total_seconds(), 1)
    if result.exception_info is not None:
        entry["outcome"] = "errored"
        entry["error"] = _error_line(
            result.exception_info.exception_type, result.exception_info.exception_message
        )
    elif entry["reward"] is None:
        entry["outcome"] = "no reward"
    elif entry["reward"] >= 1.0:
        entry["outcome"] = "passed"
    elif entry["reward"] > 0:
        entry["outcome"] = "partial"
    else:
        entry["outcome"] = "failed"
    ours = (result.agent_info.name if result.agent_info else None) == "omnicoreagent"
    if entry["status"] is None and ours and entry["outcome"] != "errored":
        # The run reports its status in result.json; without it, the run
        # never finished writing — which a reward of 0 alone does not say.
        entry["note"] = "the agent wrote no result"
    return entry


def summarize_job(job_dir: Path) -> dict[str, Any]:
    job_dir = Path(job_dir)
    trials = [
        summarize_trial(child)
        for child in sorted(job_dir.iterdir())
        if child.is_dir() and (child / "config.json").is_file()
    ]
    counts = {outcome: 0 for outcome in ("passed", "partial", "failed", "errored", "running", "no reward")}
    for trial in trials:
        counts[trial["outcome"]] += 1
    costs = [trial["cost_usd"] for trial in trials if trial["cost_usd"] is not None]
    return {
        "job": str(job_dir),
        "trials": trials,
        "totals": {
            "trials": len(trials),
            **counts,
            "pass_rate": counts["passed"] / len(trials) if trials else None,
            "cost_usd": sum(costs) if costs else None,
        },
    }


def _jobs(path: Path) -> list[Path]:
    if _is_job(path):
        return [path]
    if not path.is_dir():
        return []
    return sorted((child for child in path.iterdir() if child.is_dir() and _is_job(child)), reverse=True)


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.4f}"


def _render(summary: dict[str, Any]) -> str:
    lines = [f"job {summary['job']}"]
    for trial in summary["trials"]:
        reward = "-" if trial["reward"] is None else f"{trial['reward']:g}"
        status = trial["status"] or "-"
        # Why it ended matters when it did not succeed; "success (stop)" is noise.
        if trial["termination_reason"] and trial["status"] not in (None, "success"):
            status = f"{status} ({trial['termination_reason']})"
        steps = trial["steps"] if trial["steps"] is not None else "-"
        lines.append(
            f"  {trial['outcome']:<9} reward {reward:<4} {trial['trial']:<40} "
            f"status {status:<20} steps {steps!s:<4} cost {_money(trial['cost_usd'])}"
        )
        for detail in filter(None, [trial["error"], trial["detail"], trial["note"]]):
            lines.append(f"            {detail}")
        for failed in trial["mcp_failed"]:
            lines.append(f"            MCP server unusable: {failed}")
    totals = summary["totals"]
    lines.append(
        f"  {totals['passed']}/{totals['trials']} passed, {totals['failed']} failed, "
        f"{totals['errored']} errored, {totals['running']} running; cost {_money(totals['cost_usd'])}"
    )
    return "\n".join(lines)


@click.command("results")
@click.argument("path", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Print the summary as JSON.")
def results_command(path: Path, as_json: bool) -> None:
    """Summarize a Harbor job directory, or every job in a jobs directory."""
    jobs = _jobs(path)
    if not jobs:
        raise click.ClickException(f"There is no Harbor job in {path}.")
    summaries = [summarize_job(job) for job in jobs]
    if as_json:
        click.echo(json.dumps(summaries if len(summaries) > 1 or not _is_job(path) else summaries[0], indent=2))
        return
    click.echo("\n\n".join(_render(summary) for summary in summaries))
