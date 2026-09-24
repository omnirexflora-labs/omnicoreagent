"""``python -m omnicoreagent.cli`` works, not only the console script.

A harness starts a headless run as a subprocess. It cannot rely on the
``omnicoreagent`` script being on PATH — a checkout used through
``PYTHONPATH`` has no console script at all, and a virtual environment that
is not activated has it somewhere the harness would have to guess. Running the
module is the way that always works, so it has to work.
"""

from __future__ import annotations

import subprocess
import sys


def _module(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "omnicoreagent.cli", *arguments],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_module_runs_and_offers_the_run_command():
    finished = _module("--help")

    assert finished.returncode == 0, finished.stderr
    assert "run" in finished.stdout


def test_the_run_command_describes_itself_through_the_module():
    finished = _module("run", "--help")

    assert finished.returncode == 0, finished.stderr
    for option in ("--agent", "--approval-mode", "--budget-mode", "--timeout"):
        assert option in finished.stdout, f"{option} is not offered"


def test_a_usage_error_through_the_module_is_reported_not_crashed():
    finished = _module("run")  # no agent, no instruction

    assert finished.returncode != 0
    assert "Traceback" not in finished.stderr, finished.stderr
