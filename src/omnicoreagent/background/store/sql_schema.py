"""The SQL task store's tables: a row per entity, indexed for its queries.

Each row keeps the entity itself as JSON text in ``data``; the columns beside
it are what the store filters, orders and locks by. Types are the portable
ones — text, integer, boolean, naive UTC timestamps — so the same schema is
created on SQLite, PostgreSQL and MySQL. Keys are bounded strings because
MySQL cannot index unbounded text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskStoreTables:
    metadata: Any
    agents: Any
    tasks: Any
    schedules: Any
    runs: Any
    attempts: Any


def build_tables(prefix: str = "") -> TaskStoreTables:
    from sqlalchemy import (
        Boolean,
        Column,
        DateTime,
        Index,
        Integer,
        MetaData,
        String,
        Table,
        Text,
    )

    def key(name: str, **kwargs: Any) -> Column:
        return Column(name, String(255), **kwargs)

    metadata = MetaData()

    def named(name: str) -> str:
        return f"{prefix}{name}"

    agents = Table(
        named("background_agents"),
        metadata,
        key("agent_id", primary_key=True),
        Column("created_at", DateTime),
        Column("version", Integer, nullable=False),
        Column("data", Text, nullable=False),
    )

    tasks = Table(
        named("background_tasks"),
        metadata,
        key("task_id", primary_key=True),
        key("agent_id", nullable=False),
        Column("enabled", Boolean, nullable=False),
        Column("created_at", DateTime),
        Column("version", Integer, nullable=False),
        Column("data", Text, nullable=False),
        Index(named("background_tasks_agent"), "agent_id"),
    )

    schedules = Table(
        named("background_schedule_states"),
        metadata,
        key("task_id", primary_key=True),
        Column("next_due_at", DateTime),
        Column("paused", Boolean, nullable=False),
        Column("schedule_revision", Integer, nullable=False),
        Column("version", Integer, nullable=False),
        Column("data", Text, nullable=False),
        # What the scheduler asks for: the schedules that are due.
        Index(named("background_schedules_due"), "paused", "next_due_at"),
    )

    runs = Table(
        named("background_runs"),
        metadata,
        key("run_id", primary_key=True),
        key("task_id", nullable=False),
        key("agent_id", nullable=False),
        key("status", nullable=False),
        key("occurrence_id"),
        Column("queued_at", DateTime),
        Column("triggered_at", DateTime),
        Column("lease_expires_at", DateTime),
        Column("cancel_requested_at", DateTime),
        Column("version", Integer, nullable=False),
        Column("data", Text, nullable=False),
        # A claim reads queued runs in order; the overlap guard reads one
        # task's unfinished runs; a scheduled dispatch looks up its
        # occurrence; the lease sweeper reads what has expired.
        Index(named("background_runs_claim"), "status", "queued_at"),
        Index(named("background_runs_task_status"), "task_id", "status"),
        Index(named("background_runs_occurrence"), "task_id", "occurrence_id"),
        Index(named("background_runs_lease"), "lease_expires_at"),
    )

    attempts = Table(
        named("background_attempts"),
        metadata,
        key("attempt_id", primary_key=True),
        key("run_id", nullable=False),
        Column("attempt_number", Integer, nullable=False),
        Column("version", Integer, nullable=False),
        Column("data", Text, nullable=False),
        Index(named("background_attempts_run"), "run_id", "attempt_number"),
    )

    return TaskStoreTables(
        metadata=metadata,
        agents=agents,
        tasks=tasks,
        schedules=schedules,
        runs=runs,
        attempts=attempts,
    )
