"""The support desk load report counts a failure only when something failed.

A ramp on 2026-10-08 reported 130 "failed requests" at 100 users on one
process. 95 were requests the admission limit turned away (503, nothing
started) and 36 were resumes the server's sweep had already done (409). These
tests hold the report to telling those apart from a real failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

LOAD = Path(__file__).resolve().parent.parent / "apps" / "support_desk" / "load"
sys.path.insert(0, str(LOAD))

import loadlib  # noqa: E402
import loadtest  # noqa: E402


def _record(kind, error, http=None):
    return loadlib.Record(kind=kind, t0=10.0, seconds=1.0, http=http, error=error)


def test_a_turned_away_request_and_an_already_resumed_run_are_not_failures():
    assert loadlib.classify(_record("chat_order", None, 200)) is None
    assert loadlib.classify(_record("chat_order", "http_503", 503)) == "shed"
    assert loadlib.classify(_record("resume", "http_409", 409)) == "already"
    assert loadlib.classify(_record("approve", "http_409", 409)) == "already"
    assert loadlib.classify(_record("chat_order", "http_409", 409)) == "failed"
    assert loadlib.classify(_record("chat_refund", "http_500", 500)) == "failed"
    assert loadlib.classify(_record("chat_refund", "disconnect")) == "failed"


def test_the_concurrency_line_counts_failures_and_reports_what_was_shed():
    records = [
        _record("chat_order", None, 200),
        _record("chat_order", "http_503", 503),
        _record("resume", "http_409", 409),
        _record("chat_refund", "disconnect"),
    ]
    boundary = {"stage": 0, "users": 100, "seconds": 600, "t_start": 0.0, "t_end": 700.0}
    stage = {
        **boundary, "requests": len(records), "errors": 3,
        "failed": sum(loadlib.classify(r) == "failed" for r in records),
        "shed": sum(loadlib.classify(r) == "shed" for r in records),
        "resources": {"lag_max_ms": 10.0},
    }
    result = {
        "stages": [stage], "mode": "ramp", "samples": [], "started": 0.0, "load_ended": 700.0,
        "overhead": {"overall": {"step_overhead_ms": {"p95": 50.0}}},
        "correctness": {"ok": True},
    }
    lines = loadtest.finish_lines(result, boundary)
    failed = next(line for line in lines if line["measure"].startswith("failed requests"))
    shed = next(line for line in lines if line["measure"].startswith("turned away"))
    assert failed["value"] == 1 and failed["verdict"] == "MISS"
    assert shed["value"] == "1 of 4 (25.0%)" and shed["verdict"] == "-"
