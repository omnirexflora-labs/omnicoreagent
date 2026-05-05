from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, model_validator
import json


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    TOOL_CALLING = "tool_calling"
    OBSERVING = "observing"
    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"


class ToolFunction(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str = "function"
    function: ToolFunction


class ToolCallMetadata(BaseModel):
    has_tool_calls: bool = False
    tool_calls: list[ToolCall] = []
    tool_call_id: UUID | None = None
    agent_name: str | None = None


class Message(BaseModel):
    role: str
    content: str
    tool_call_id: Optional[str] = None
    tool_calls: Optional[str] = None
    metadata: Optional[ToolCallMetadata] = None
    timestamp: Optional[str] = None

    @model_validator(mode="before")
    def ensure_content_is_string(cls, values):
        c = values.get("content")
        if not isinstance(c, str):
            try:
                values["content"] = json.dumps(c, ensure_ascii=False)
            except Exception:
                values["content"] = str(c)
        return values


class ParsedResponse(BaseModel):
    action: bool | None = None
    data: str | None = None
    error: str | None = None
    answer: str | None = None
    tool_calls: bool | None = None
    agent_calls: bool | None = None


class ToolCallResult(BaseModel):
    tool_executor: Any
    tool_name: str
    tool_args: dict
    tool_call_id: str | None = None


class ToolError(BaseModel):
    observation: str
    tool_name: str
    tool_args: dict | None = None


class ToolData(BaseModel):
    action: bool
    tool_name: str | None = None
    tool_args: dict | None = None
    error: str | None = None


class ToolCallRecord(BaseModel):
    tool_name: str
    tool_args: str
    observation: str


class ToolParameter(BaseModel):
    type: str
    description: str


class ToolRegistryEntry(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter] = []


class ToolExecutorConfig(BaseModel):
    handler: Any
    tool_data: dict[str, Any]
    available_tools: dict[str, Any]


class LoopDetectorConfig(BaseModel):
    max_repeats: int = 3
    similarity_threshold: float = 0.9


class SessionState(BaseModel):
    messages: list[Message]
    state: AgentState
    loop_detector: Any
    assistant_with_tool_calls: dict | None
    pending_tool_responses: list[dict]


class ContextInclusion(str, Enum):
    NONE = "none"
    THIS_SERVER = "thisServer"
    ALL_SERVERS = "allServers"


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    TOOL_CALLING = "tool_calling"
    OBSERVING = "observing"
    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"
