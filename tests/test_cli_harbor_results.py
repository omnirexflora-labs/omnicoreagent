"""``omnicoreagent harbor results``: what a job did, without opening its logs.

Harbor's own summary command is a removed shim, and its exit code is 0 for a job
whose every trial errored. The fixture is three real trials of this adapter,
copied from the server (their files unchanged): one that passed, one whose run
wrote nothing (the release had no ``cli`` module), and one whose install failed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

harbor = pytest.importorskip("harbor", reason="needs the harbor extra")

from harbor.models.trial.result import TrialResult  # noqa: E402

from omnicoreagent.cli import cli  # noqa: E402
from omnicoreagent.cli.harbor_results import summarize_job  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "harbor_job"
PASSED = "house-report-skill__6akmi4n"
NO_OUTPUT = "receipts-subtotal__YQwKwim"
ERRORED = "receipts-subtotal__Fg2eKg2"


def test_the_fixture_is_harbors_own_shape():
    """Read by Harbor's model, so a change to its format fails here first."""
    for trial in (PASSED, NO_OUTPUT, ERRORED):
        TrialResult.model_validate_json((FIXTURE / trial / "result.json").read_text())


def _trials(summary) -> dict:
    return {trial["trial"]: trial for trial in summary["trials"]}


def test_a_passed_trial_says_what_it_cost_and_where_its_trajectory_is():
    trial = _trials(summarize_job(FIXTURE))[PASSED]

    assert trial["outcome"] == "passed"
    assert trial["reward"] == 1.0
    assert trial["task"] == "omnicoreagent-labs/house-report-skill"
    assert trial["status"] == "success"
    assert trial["exit_code"] == 0
    assert trial["cost_usd"] == pytest.approx(0.0124463)
    assert trial["input_tokens"] == 9436
    assert trial["steps"] and trial["steps"] > 1
    assert trial["trajectory"].endswith(f"{PASSED}/agent/trajectory.json")
    assert trial["error"] is None


def test_a_trial_whose_run_wrote_nothing_says_so():
    """Reward 0 and no result from the run is not the same as a wrong answer."""
    trial = _trials(summarize_job(FIXTURE))[NO_OUTPUT]

    assert trial["outcome"] == "failed"
    assert trial["reward"] == 0.0
    assert trial["status"] is None
    assert trial["note"] == "the agent wrote no result"
    assert trial["trajectory"] is None


def test_an_errored_trial_names_the_error_not_the_command_that_raised_it():
    trial = _trials(summarize_job(FIXTURE))[ERRORED]

    assert trial["outcome"] == "errored"
    assert trial["reward"] is None
    assert trial["error"].startswith("NonZeroAgentExitCodeError: ")
    assert "Invalid requirement" in trial["error"]
    assert "python3 -m venv" not in trial["error"]
    assert trial["note"] is None, "the error already says why there is no result"


def test_the_totals_count_an_error_as_not_passed():
    totals = summarize_job(FIXTURE)["totals"]

    assert totals["trials"] == 3
    assert totals["passed"] == 1
    assert totals["failed"] == 1
    assert totals["errored"] == 1
    assert totals["pass_rate"] == pytest.approx(1 / 3)
    assert totals["cost_usd"] == pytest.approx(0.0124463)


def test_a_trial_still_running_is_listed_as_running(tmp_path):
    job = tmp_path / "job"
    shutil.copytree(FIXTURE, job)
    (job / "running-task__abc").mkdir()
    (job / "running-task__abc" / "config.json").write_text("{}")

    trial = _trials(summarize_job(job))["running-task__abc"]

    assert trial["outcome"] == "running"


def test_a_failed_mcp_server_is_shown_beside_the_reward(tmp_path):
    job = tmp_path / "job"
    shutil.copytree(FIXTURE, job)
    result_path = job / PASSED / "result.json"
    result = json.loads(result_path.read_text())
    result["agent_result"]["metadata"]["omnicoreagent_mcp_failed"] = ["rates: Connection closed"]
    result_path.write_text(json.dumps(result))

    trial = _trials(summarize_job(job))[PASSED]

    assert trial["mcp_failed"] == ["rates: Connection closed"]


def test_a_directory_of_jobs_is_read_job_by_job(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "jobs" / "2026-09-25__06-00-00")
    shutil.copytree(FIXTURE, tmp_path / "jobs" / "2026-09-25__07-00-00")

    result = CliRunner().invoke(cli, ["harbor", "results", str(tmp_path / "jobs"), "--json"])

    assert result.exit_code == 0, result.output
    jobs = json.loads(result.output)
    assert [Path(job["job"]).name for job in jobs] == [
        "2026-09-25__07-00-00",
        "2026-09-25__06-00-00",
    ], "newest first"


def test_the_command_prints_a_table_a_person_can_read():
    result = CliRunner().invoke(cli, ["harbor", "results", str(FIXTURE)])

    assert result.exit_code == 0, result.output
    assert PASSED in result.output
    assert "errored" in result.output
    assert "Invalid requirement" in result.output
    assert "1/3 passed" in result.output


def test_a_directory_that_is_not_a_job_is_a_sentence(tmp_path):
    result = CliRunner().invoke(cli, ["harbor", "results", str(tmp_path)])

    assert result.exit_code != 0
    assert "no Harbor job" in result.output
