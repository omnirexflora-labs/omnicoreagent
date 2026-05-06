from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import json
from typing import Any
from uuid import UUID, uuid4


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    TOOL_CALLING = "tool_calling"
    OBSERVING = "observing"
    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"


class ContextInclusion(str, Enum):
    NONE = "none"
    THIS_SERVER = "thisServer"
    ALL_SERVERS = "allServers"


def _to_plain(value: Any, *, exclude_none: bool = False) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value):
        return {
            key: _to_plain(item, exclude_none=exclude_none)
            for key, item in asdict(value).items()
            if not (exclude_none and item is None)
        }
    if isinstance(value, dict):
        return {
            key: _to_plain(item, exclude_none=exclude_none)
            for key, item in value.items()
            if not (exclude_none and item is None)
        }
    if isinstance(value, list):
        return [_to_plain(item, exclude_none=exclude_none) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item, exclude_none=exclude_none) for item in value]
    return value


class SerializableRecord:
    @classmethod
    def model_validate(cls, value: Any):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise ValueError(f"{cls.__name__} requires a dict or {cls.__name__} instance")

    def model_dump(self, *, exclude_none: bool = False, **_: Any) -> dict[str, Any]:
        return _to_plain(self, exclude_none=exclude_none)

    def dict(self, *, exclude_none: bool = False, **kwargs: Any) -> dict[str, Any]:
        return self.model_dump(exclude_none=exclude_none, **kwargs)

    def json(self, *, exclude_none: bool = False, **kwargs: Any) -> str:
        return json.dumps(self.model_dump(exclude_none=exclude_none, **kwargs))


@dataclass
class ToolFunction(SerializableRecord):
    name: str
    arguments: str


@dataclass
class ToolCall(SerializableRecord):
    function: ToolFunction | dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    type: str = "function"

    def __post_init__(self):
        if isinstance(self.function, dict):
            self.function = ToolFunction(**self.function)


@dataclass
class ToolCallMetadata(SerializableRecord):
    has_tool_calls: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: UUID | str | None = None
    agent_name: str | None = None

    def __post_init__(self):
        self.tool_calls = [
            call if isinstance(call, ToolCall) else ToolCall(**call)
            for call in self.tool_calls
        ]


@dataclass
class Message(SerializableRecord):
    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: str | None = None
    metadata: ToolCallMetadata | dict[str, Any] | None = None
    timestamp: str | None = None

    def __post_init__(self):
        if not isinstance(self.content, str):
            try:
                self.content = json.dumps(self.content, ensure_ascii=False)
            except Exception:
                self.content = str(self.content)
        if isinstance(self.metadata, dict):
            self.metadata = ToolCallMetadata(**self.metadata)


@dataclass
class ParsedResponse(SerializableRecord):
    action: bool | None = None
    data: str | None = None
    error: str | None = None
    answer: str | None = None
    tool_calls: bool | None = None
    agent_calls: bool | None = None


@dataclass
class ToolCallResult(SerializableRecord):
    tool_executor: Any
    tool_name: str
    tool_args: dict
    tool_call_id: str | None = None


@dataclass
class ToolError(SerializableRecord):
    observation: str
    tool_name: str
    tool_args: dict | None = None


@dataclass
class ToolCallRecord(SerializableRecord):
    tool_name: str
    tool_args: str
    observation: str


@dataclass
class LoopDetectorConfig(SerializableRecord):
    max_repeats: int = 3
    similarity_threshold: float = 0.9


@dataclass
class SessionState(SerializableRecord):
    messages: list[Message]
    state: AgentState
    loop_detector: Any
    assistant_with_tool_calls: dict | None
    pending_tool_responses: list[dict]
