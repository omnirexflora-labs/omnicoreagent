from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Type
from uuid import uuid4

_CURRENT_EVENT_RUN_ID: ContextVar[str | None] = ContextVar(
    "omnicoreagent_event_run_id",
    default=None,
)


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_RESULT = "tool_call_result"
    TOOL_CALL_ERROR = "tool_call_error"
    FINAL_ANSWER = "final_answer"
    AGENT_THOUGHT = "agent_thought"
    SUB_AGENT_CALL_STARTED = "sub_agent_call_started"
    SUB_AGENT_CALL_RESULT = "sub_agent_call_result"
    SUB_AGENT_CALL_ERROR = "sub_agent_call_error"
    BACKGROUND_TASK_STARTED = "background_task_started"
    BACKGROUND_TASK_COMPLETED = "background_task_completed"
    BACKGROUND_TASK_ERROR = "background_task_error"
    BACKGROUND_AGENT_STATUS = "background_agent_status"


class SerializableRecord:
    def model_dump(self) -> dict[str, Any]:
        return _to_plain(self)

    def dict(self) -> dict[str, Any]:
        return self.model_dump()

    def json(self) -> str:
        return json.dumps(self.model_dump())


@dataclass
class UserMessagePayload(SerializableRecord):
    message: str


@dataclass
class AgentMessagePayload(SerializableRecord):
    message: str


@dataclass
class ToolCallStartedPayload(SerializableRecord):
    tool_name: str
    tool_args: str | dict[str, Any]
    tool_call_id: str | None = None


@dataclass
class ToolCallResultPayload(SerializableRecord):
    tool_name: str
    tool_args: str | dict[str, Any]
    result: str
    tool_call_id: str | None = None


@dataclass
class ToolCallErrorPayload(SerializableRecord):
    tool_name: str
    error_message: str


@dataclass
class FinalAnswerPayload(SerializableRecord):
    message: str


@dataclass
class AgentThoughtPayload(SerializableRecord):
    message: str


@dataclass
class SubAgentCallStartedPayload(SerializableRecord):
    agent_name: str
    session_id: str
    timestamp: str
    run_count: int
    kwargs: dict[str, Any]


@dataclass
class SubAgentCallResultPayload(SerializableRecord):
    agent_name: str
    session_id: str
    timestamp: str
    run_count: int
    result: Any


@dataclass
class SubAgentCallErrorPayload(SerializableRecord):
    agent_name: str
    session_id: str
    timestamp: str
    error: str
    error_count: int


@dataclass
class BackgroundTaskStartedPayload(SerializableRecord):
    agent_id: str
    session_id: str
    timestamp: str
    run_count: int
    kwargs: dict[str, Any]


@dataclass
class BackgroundTaskCompletedPayload(SerializableRecord):
    agent_id: str
    session_id: str
    timestamp: str
    run_count: int
    result: Any


@dataclass
class BackgroundTaskErrorPayload(SerializableRecord):
    agent_id: str
    session_id: str
    timestamp: str
    error: str
    error_count: int


@dataclass
class BackgroundAgentStatusPayload(SerializableRecord):
    agent_id: str
    status: str
    timestamp: str
    session_id: str | None = None
    last_run: str | None = None
    run_count: int | None = None
    error_count: int | None = None
    error: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    event: str | None = None
    run_status: str | None = None
    attempt: int | None = None
    sequence: int | None = None
    workspace_path: str | None = None
    worker_id: str | None = None
    lease_generation: int | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    occurrence_id: str | None = None
    due_at: str | None = None


EventPayload = (
    UserMessagePayload
    | AgentMessagePayload
    | ToolCallStartedPayload
    | ToolCallResultPayload
    | ToolCallErrorPayload
    | FinalAnswerPayload
    | AgentThoughtPayload
    | SubAgentCallStartedPayload
    | SubAgentCallResultPayload
    | SubAgentCallErrorPayload
    | BackgroundTaskStartedPayload
    | BackgroundTaskCompletedPayload
    | BackgroundTaskErrorPayload
    | BackgroundAgentStatusPayload
)


EVENT_PAYLOAD_MAP: dict[EventType, Type[Any]] = {
    EventType.USER_MESSAGE: UserMessagePayload,
    EventType.AGENT_MESSAGE: AgentMessagePayload,
    EventType.TOOL_CALL_STARTED: ToolCallStartedPayload,
    EventType.TOOL_CALL_RESULT: ToolCallResultPayload,
    EventType.TOOL_CALL_ERROR: ToolCallErrorPayload,
    EventType.FINAL_ANSWER: FinalAnswerPayload,
    EventType.AGENT_THOUGHT: AgentThoughtPayload,
    EventType.SUB_AGENT_CALL_STARTED: SubAgentCallStartedPayload,
    EventType.SUB_AGENT_CALL_RESULT: SubAgentCallResultPayload,
    EventType.SUB_AGENT_CALL_ERROR: SubAgentCallErrorPayload,
    EventType.BACKGROUND_TASK_STARTED: BackgroundTaskStartedPayload,
    EventType.BACKGROUND_TASK_COMPLETED: BackgroundTaskCompletedPayload,
    EventType.BACKGROUND_TASK_ERROR: BackgroundTaskErrorPayload,
    EventType.BACKGROUND_AGENT_STATUS: BackgroundAgentStatusPayload,
}


def _to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def current_event_run_id() -> str | None:
    return _CURRENT_EVENT_RUN_ID.get()


def set_event_run_id(run_id: str | None) -> Token:
    return _CURRENT_EVENT_RUN_ID.set(run_id)


def reset_event_run_id(token: Token) -> None:
    _CURRENT_EVENT_RUN_ID.reset(token)


@dataclass
class Event:
    type: EventType | str
    payload: EventPayload | dict[str, Any]
    agent_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid4()))
    sequence: int | None = None
    run_id: str | None = field(default_factory=current_event_run_id)

    def __post_init__(self):
        self.type = EventType(self.type)
        payload_type = EVENT_PAYLOAD_MAP[self.type]
        if isinstance(self.payload, dict):
            self.payload = payload_type(**self.payload)
        validate_event(self)

    def model_dump(self) -> dict[str, Any]:
        return {
            "type": _to_plain(self.type),
            "payload": _to_plain(self.payload),
            "timestamp": _to_plain(self.timestamp),
            "agent_name": self.agent_name,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "run_id": self.run_id,
        }

    def dict(self) -> dict[str, Any]:
        return self.model_dump()

    def json(self) -> str:
        return json.dumps(self.model_dump())

    @classmethod
    def parse_raw(cls, raw: str) -> Event:
        data = json.loads(raw)
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            data["timestamp"] = datetime.fromisoformat(timestamp)
        return cls(**data)


def validate_event(event: Event):
    expected_type = EVENT_PAYLOAD_MAP[event.type]
    if not isinstance(event.payload, expected_type):
        raise TypeError(
            f"Payload mismatch: Expected {expected_type} for "
            f"{event.type}, got {type(event.payload)}"
        )


class BaseEventStore(ABC):
    @abstractmethod
    async def append(self, session_id: str, event: Event) -> None:
        validate_event(event)
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def get_events(self, session_id: str) -> list[Event]:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def stream(self, session_id: str) -> AsyncIterator[Event]:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def get_stream_cursor(self, session_id: str) -> str | None:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def stream_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> AsyncIterator[Event]:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def get_events_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> list[Event]:
        raise NotImplementedError("Subclasses must implement this method")
