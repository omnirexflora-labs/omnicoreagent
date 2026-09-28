"""Misfire policies do what their names say.

Found writing Background agents (D7): `skip_missed` queued a run for a missed
time exactly like `run_once`. The maintainer's decision (2026-09-28): the
default becomes `run_once` (what the default already did: after downtime, one
run for the missed time), and `skip_missed` skips an occurrence missed by more
than a minute and waits for the next.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from omnicoreagent.background.models import (
    MisfirePolicy,
    ScheduleSpec,
    schedule_due_occurrences,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _interval(policy=None) -> ScheduleSpec:
    extra = {"misfire_policy": policy} if policy else {}
    return ScheduleSpec(type="interval", seconds=3600, **extra)


def test_the_default_runs_once_for_missed_time():
    schedule = _interval()
    assert schedule.misfire_policy == MisfirePolicy.RUN_ONCE
    due, next_due = schedule_due_occurrences(schedule, NOW - timedelta(hours=5), NOW)
    assert due == [NOW - timedelta(hours=5)] and next_due > NOW


def test_skip_missed_skips_an_occurrence_missed_long_ago():
    due, next_due = schedule_due_occurrences(
        _interval("skip_missed"), NOW - timedelta(hours=5), NOW
    )
    assert due == [] and next_due > NOW


def test_skip_missed_still_runs_an_occurrence_that_is_just_due():
    just = NOW - timedelta(seconds=5)
    due, _ = schedule_due_occurrences(_interval("skip_missed"), just, NOW)
    assert due == [just]
