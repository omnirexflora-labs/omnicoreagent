from abc import ABC, abstractmethod
from typing import AsyncIterator, Any, Dict, List, Optional, Union
from enum import Enum
from datetime import datetime
from uuid import uuid4
from typing import Type
from pydantic import BaseModel, Field


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
    # Background agent events
    BACKGROUND_TASK_STARTED = "background_task_started"
    BACKGROUND_TASK_COMPLETED = "background_task_completed"
    BACKGROUND_TASK_ERROR = "background_task_error"
    BACKGROUND_AGENT_STATUS = "background_agent_status"


class UserMessagePayload(BaseModel):
    message: str


class AgentMessagePayload(BaseModel):
    message: str


class ToolCallStartedPayload(BaseModel):
    tool_name: str
    tool_args: str | Dict[str, Any]
    tool_call_id: Optional[str] = None


class ToolCallResultPayload(BaseModel):
    tool_name: str
    tool_args: str | Dict[str, Any]
    tool_call_id: Optional[str] = None
    result: str


class ToolCallErrorPayload(BaseModel):
    tool_name: str
    error_message: str


class FinalAnswerPayload(BaseModel):
    message: str


class AgentThoughtPayload(BaseModel):
    message: str


class SubAgentCallStartedPayload(BaseModel):
    agent_name: str
    session_id: str
    timestamp: str
    run_count: int
    kwargs: Dict[str, Any]


class SubAgentCallResultPayload(BaseModel):
    agent_name: str
    session_id: str
    timestamp: str
    run_count: int
    result: Any


class SubAgentCallErrorPayload(BaseModel):
    agent_name: str
    session_id: str
    timestamp: str
    error: str
    error_count: int


# Background agent payload models
class BackgroundTaskStartedPayload(BaseModel):
    agent_id: str
    session_id: str
    timestamp: str
    run_count: int
    kwargs: Dict[str, Any]


class BackgroundTaskCompletedPayload(BaseModel):
    agent_id: str
    session_id: str
    timestamp: str
    run_count: int
    result: Any


class BackgroundTaskErrorPayload(BaseModel):
    agent_id: str
    session_id: str
    timestamp: str
    error: str
    error_count: int


class BackgroundAgentStatusPayload(BaseModel):
    agent_id: str
    status: str
    timestamp: str
    session_id: Optional[str] = None
    last_run: Optional[str] = None
    run_count: Optional[int] = None
    error_count: Optional[int] = None
    error: Optional[str] = None


EventPayload = Union[
    UserMessagePayload,
    AgentMessagePayload,
    ToolCallStartedPayload,
    ToolCallResultPayload,
    ToolCallErrorPayload,
    FinalAnswerPayload,
    AgentThoughtPayload,
    SubAgentCallStartedPayload,
    SubAgentCallResultPayload,
    SubAgentCallErrorPayload,
    BackgroundTaskStartedPayload,
    BackgroundTaskCompletedPayload,
    BackgroundTaskErrorPayload,
    BackgroundAgentStatusPayload,
]


class Event(BaseModel):
    type: EventType
    payload: EventPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_name: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))


EVENT_PAYLOAD_MAP: dict[EventType, Type[BaseModel]] = {
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


def validate_event(event: Event):
    expected_type = EVENT_PAYLOAD_MAP[event.type]
    if not isinstance(event.payload, expected_type):
        raise TypeError(
            f"Payload mismatch: Expected {expected_type} for {event.type}, got {type(event.payload)}"
        )


class BaseEventStore(ABC):
    @abstractmethod
    async def append(self, session_id: str, event: Event) -> None:
        validate_event(event)
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def get_events(self, session_id: str) -> List[Event]:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def stream(self, session_id: str) -> AsyncIterator[Event]:
        raise NotImplementedError("Subclasses must implement this method")
