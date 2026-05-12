"""Typed records for durable background execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omnicoreagent.background.errors import InvalidScheduleError


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_stable_id(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    if not _SAFE_ID.match(value):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, '_', '-', and '.'"
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, use_enum_values=False
    )


class TaskStoreBackend(str, Enum):
    IN_MEMORY = "in_memory"
    SQL = "sql"
    REDIS = "redis"
    MONGODB = "mongodb"


class ScheduleType(str, Enum):
    MANUAL = "manual"
    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"


class MisfirePolicy(str, Enum):
    SKIP_MISSED = "skip_missed"
    RUN_ONCE = "run_once"
    QUEUE_ALL = "queue_all"


class BackoffPolicy(str, Enum):
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class OverlapPolicy(str, Enum):
    SKIP_IF_RUNNING = "skip_if_running"
    QUEUE_NEXT = "queue_next"
    CANCEL_PREVIOUS = "cancel_previous"
    ALLOW_PARALLEL = "allow_parallel"


class SessionMode(str, Enum):
    TASK = "task"
    RUN = "run"
    FIXED = "fixed"


class RunStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMEOUT,
    RunStatus.SKIPPED,
}

TERMINAL_EVENT_NAMES = {
    "background_run_completed",
    "background_run_failed",
    "background_run_timeout",
    "background_run_cancelled",
    "background_run_skipped",
}

INITIAL_EVENT_NAMES = {
    "background_run_queued",
    "background_run_skipped",
}

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.CLAIMED,
    RunStatus.RUNNING,
    RunStatus.RETRYING,
}


class AttemptStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AttemptReason(str, Enum):
    INITIAL = "initial"
    RETRY = "retry"
    RECOVERY = "recovery"
    LEASE_EXPIRED = "lease_expired"


class TriggerType(str, Enum):
    MANUAL = "manual"
    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"


class RetryPolicy(StrictModel):
    max_retries: int = 0
    initial_delay_seconds: int = 30
    max_delay_seconds: int = 300
    backoff: BackoffPolicy = BackoffPolicy.FIXED
    retry_on: list[str] = Field(default_factory=lambda: ["exception", "timeout"])

    @model_validator(mode="after")
    def validate_policy(self) -> "RetryPolicy":
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be >= 0")
        if (
            self.initial_delay_seconds > 0
            and self.max_delay_seconds < self.initial_delay_seconds
        ):
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")
        return self


class SessionPolicy(StrictModel):
    mode: SessionMode = SessionMode.TASK
    session_id: str | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> "SessionPolicy":
        if self.mode == SessionMode.FIXED and not self.session_id:
            raise ValueError("fixed session policy requires session_id")
        return self


class WorkspacePolicy(StrictModel):
    namespace_template: str = "background/{agent_id}/{task_id}/{run_id}"
    write_run_json: bool = True
    write_events_jsonl: bool = True


class ScheduleSpec(StrictModel):
    type: ScheduleType
    seconds: int | None = None
    expression: str | None = None
    run_at: datetime | None = None
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None
    jitter_seconds: int | None = None
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP_MISSED

    @field_validator("run_at", "start_at", "end_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @model_validator(mode="after")
    def validate_schedule(self) -> "ScheduleSpec":
        if self.type == ScheduleType.INTERVAL and (self.seconds is None or self.seconds <= 0):
            raise InvalidScheduleError("interval schedule requires seconds > 0")
        if self.type == ScheduleType.CRON:
            if not self.expression:
                raise InvalidScheduleError("cron schedule requires expression")
            if len(self.expression.split()) != 5:
                raise InvalidScheduleError("cron expression must have five fields")
        if self.type == ScheduleType.ONCE and self.run_at is None:
            raise InvalidScheduleError("once schedule requires run_at")
        if self.type == ScheduleType.MANUAL:
            if self.seconds is not None or self.expression is not None or self.run_at is not None:
                raise InvalidScheduleError("manual schedule does not accept scheduler fields")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise InvalidScheduleError("end_at must be after start_at")
        if self.jitter_seconds is not None and self.jitter_seconds < 0:
            raise InvalidScheduleError("jitter_seconds must be >= 0")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise InvalidScheduleError(f"unknown schedule timezone: {self.timezone}") from exc
        return self


def _parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise InvalidScheduleError("empty cron field segment")
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step <= 0:
            raise InvalidScheduleError("cron step must be positive")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        if minimum == 0 and maximum == 6:
            if start == 7:
                start = 0
            if end == 7:
                end = 0
        if start < minimum or start > maximum or end < minimum or end > maximum:
            raise InvalidScheduleError(
                f"cron value must be between {minimum} and {maximum}"
            )
        if start <= end:
            values.update(range(start, end + 1, step))
        else:
            wrapped = list(range(start, maximum + 1)) + list(range(minimum, end + 1))
            values.update(value for index, value in enumerate(wrapped) if index % step == 0)
    return values


def _cron_matches(expression: str, value: datetime) -> bool:
    minute, hour, day, month, day_of_week = expression.split()
    cron_day_of_week = (value.weekday() + 1) % 7
    return (
        value.minute in _parse_cron_field(minute, 0, 59)
        and value.hour in _parse_cron_field(hour, 0, 23)
        and value.day in _parse_cron_field(day, 1, 31)
        and value.month in _parse_cron_field(month, 1, 12)
        and cron_day_of_week in _parse_cron_field(day_of_week, 0, 6)
    )


def next_cron_due(expression: str, after: datetime, timezone_name: str = "UTC") -> datetime:
    tz = ZoneInfo(timezone_name)
    cursor = (ensure_utc(after) or utc_now()).astimezone(tz)
    cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = cursor + timedelta(days=366)
    while cursor <= deadline:
        if _cron_matches(expression, cursor):
            return cursor.astimezone(timezone.utc)
        cursor += timedelta(minutes=1)
    raise InvalidScheduleError("cron expression has no due time within one year")


def initial_schedule_due(schedule: ScheduleSpec, now: datetime | None = None) -> datetime | None:
    reference = ensure_utc(now) or utc_now()
    if schedule.start_at and schedule.start_at > reference:
        reference = schedule.start_at
    if schedule.type == ScheduleType.MANUAL:
        return None
    if schedule.type == ScheduleType.ONCE:
        return schedule.run_at
    if schedule.type == ScheduleType.INTERVAL:
        due = reference + timedelta(seconds=schedule.seconds or 0)
    elif schedule.type == ScheduleType.CRON:
        due = next_cron_due(
            schedule.expression or "* * * * *",
            reference - timedelta(minutes=1),
            schedule.timezone,
        )
    else:
        return None
    if schedule.end_at and due > schedule.end_at:
        return None
    return due


def next_schedule_due(
    schedule: ScheduleSpec,
    previous_due_at: datetime,
    now: datetime | None = None,
) -> datetime | None:
    previous = ensure_utc(previous_due_at) or utc_now()
    reference = ensure_utc(now) or utc_now()
    if schedule.misfire_policy == MisfirePolicy.SKIP_MISSED and previous < reference:
        previous = reference
    if schedule.type in {ScheduleType.MANUAL, ScheduleType.ONCE}:
        return None
    if schedule.type == ScheduleType.INTERVAL:
        due = previous + timedelta(seconds=schedule.seconds or 0)
    elif schedule.type == ScheduleType.CRON:
        due = next_cron_due(
            schedule.expression or "* * * * *", previous, schedule.timezone
        )
    else:
        return None
    if schedule.end_at and due > schedule.end_at:
        return None
    return due


class BackgroundAgentSpec(StrictModel):
    agent_id: str
    name: str | None = None
    system_instruction: str | None = None
    llm_model_config: dict[str, Any] | None = Field(default=None, alias="model_config")
    agent_config: dict[str, Any] = Field(default_factory=dict)
    mcp_tools: list[dict[str, Any]] = Field(default_factory=list)
    local_tools_ref: str | None = None
    workspace_config: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        return validate_stable_id(value, "agent_id")

    @model_validator(mode="after")
    def default_name(self) -> "BackgroundAgentSpec":
        if self.name is None:
            self.name = self.agent_id
        return self


class BackgroundTaskSpec(StrictModel):
    task_id: str
    agent_id: str
    query: str
    schedule: ScheduleSpec
    enabled: bool = True
    timeout_seconds: int | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    overlap_policy: OverlapPolicy = OverlapPolicy.SKIP_IF_RUNNING
    session_policy: SessionPolicy = Field(default_factory=SessionPolicy)
    workspace_policy: WorkspacePolicy = Field(default_factory=WorkspacePolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        return validate_stable_id(value, "task_id")

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        return validate_stable_id(value, "agent_id")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("query is required")
        return value

    @model_validator(mode="after")
    def validate_timeout(self) -> "BackgroundTaskSpec":
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        return self


class BackgroundScheduleState(StrictModel):
    task_id: str
    next_due_at: datetime | None = None
    last_due_at: datetime | None = None
    last_dispatched_at: datetime | None = None
    paused: bool = False
    schedule_revision: int = 1
    misfire_cursor: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        return validate_stable_id(value, "task_id")

    @field_validator("next_due_at", "last_due_at", "last_dispatched_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class BackgroundRun(StrictModel):
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    task_id: str
    agent_id: str
    status: RunStatus = RunStatus.QUEUED
    attempt: int = 0
    max_attempts: int = 1
    query_snapshot: str
    trigger_type: TriggerType
    triggered_at: datetime = Field(default_factory=utc_now)
    due_at: datetime | None = None
    occurrence_id: str | None = None
    trigger_metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str
    workspace_path: str
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_generation: int = 0
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    queued_at: datetime | None = Field(default_factory=utc_now)
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error: str | None = None
    result_preview: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_id", "task_id", "agent_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return validate_stable_id(value, info.field_name)

    @field_validator(
        "triggered_at",
        "due_at",
        "lease_expires_at",
        "heartbeat_at",
        "queued_at",
        "claimed_at",
        "started_at",
        "finished_at",
        "cancel_requested_at",
    )
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class BackgroundAttempt(StrictModel):
    attempt_id: str = Field(default_factory=lambda: f"attempt_{uuid4().hex}")
    run_id: str
    attempt_number: int
    reason: AttemptReason = AttemptReason.INITIAL
    status: AttemptStatus = AttemptStatus.RUNNING
    worker_id: str
    lease_token: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    error: str | None = None
    retry_delay_seconds: int | None = None

    @field_validator("attempt_id", "run_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return validate_stable_id(value, info.field_name)


class TaskStoreConfig(StrictModel):
    backend: TaskStoreBackend
    prefix: str | None = None
    connect_timeout: float | None = None
    url: str | None = None
    uri: str | None = None
    database: str | None = None
    collection_prefix: str | None = None

    @model_validator(mode="after")
    def validate_config(self) -> "TaskStoreConfig":
        if self.backend == TaskStoreBackend.REDIS and not self.url:
            raise ValueError("redis task store requires url")
        if self.backend == TaskStoreBackend.MONGODB:
            if not self.uri or not self.database:
                raise ValueError("mongodb task store requires uri and database")
        if self.backend in {TaskStoreBackend.SQL, TaskStoreBackend.IN_MEMORY} and self.uri:
            raise ValueError("uri is only valid for mongodb task store")
        return self


def build_session_id(task: BackgroundTaskSpec, run_id: str) -> str:
    if task.session_policy.mode == SessionMode.TASK:
        return f"background:{task.agent_id}:{task.task_id}"
    if task.session_policy.mode == SessionMode.RUN:
        return f"background:{run_id}"
    if task.session_policy.session_id:
        return task.session_policy.session_id
    raise ValueError("fixed session policy requires session_id")


def build_workspace_path(task: BackgroundTaskSpec, run_id: str) -> str:
    return task.workspace_policy.namespace_template.format(
        agent_id=task.agent_id,
        task_id=task.task_id,
        run_id=run_id,
    )


def build_occurrence_id(
    schedule_type: ScheduleType, schedule_revision: int, due_at: datetime
) -> str:
    normalized = ensure_utc(due_at)
    assert normalized is not None
    return f"{schedule_type.value}:{schedule_revision}:{normalized.isoformat()}"


def coerce_model(model_type, value):
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type(**value)
    raise TypeError(f"Expected {model_type.__name__} or dict")
