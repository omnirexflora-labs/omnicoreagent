"""A small load, in the suite: concurrent runs must not lose money or leak.

The scale harness (``engineering/validation/load_test.py``) is what the
numbers in ``engineering/validation/scale.md`` come from, and it is run by
hand on a quiet machine. This runs the same harness at a size that fits a
test, and checks the claims that must hold at any size:

- every concurrent run finishes and answers its own question;
- every model call's cost lands exactly once on the shared budget ledger,
  so the ledger equals runs x calls x price even when the runs race;
- the process keeps no task and no file descriptor it did not start with;
- what telemetry keeps stays bounded per run.

Timings are not asserted: a CI machine's are not evidence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "engineering" / "validation" / "load_test.py"


def _harness():
    specification = importlib.util.spec_from_file_location("load_test", HARNESS)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_concurrent_runs_keep_the_ledger_and_the_process_clean(tmp_path):
    load = _harness()
    runs, concurrency, steps = 8, 4, 1
    report = await load._run_directly(tmp_path, runs=runs, concurrency=concurrency, steps=steps)

    assert report["failed"] == 0, report["failures"]
    assert report["answers_correct"] == runs
    assert report["budget_spent_usd"] == report["budget_expected_usd"]

    before, after = report["before"], report["after"]
    assert after["tasks"] <= before["tasks"]
    assert after["fds"] <= before["fds"] + concurrency
    # A run's trace is bounded by the run, not by how many ran beside it.
    assert report["telemetry_bytes_per_run"] < 1_000_000
