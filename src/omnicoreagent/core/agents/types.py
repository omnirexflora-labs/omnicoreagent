# types.py
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator, model_validator
import json
from omnicoreagent.core.utils import RobustLoopDetector


class AgentConfig(BaseModel):
    agent_name: str
    request_limit: int = Field(default=0, description="0 = unlimited (production mode)")
    total_tokens_limit: int = Field(
        default=0, description="0 = unlimited (production mode)"
    )
    max_steps: int = Field(gt=0, le=1000)
    tool_call_timeout: int = Field(gt=1, le=1000)
    enable_tools_knowledge_base: bool = Field(
        default=False, description="enable_tools_knowledge_base"
    )

    # --- Memory Retrieval Config ---
    memory_config: dict = {"mode": "sliding_window", "value": 10000}
    memory_results_limit: int = Field(
        default=5, ge=1, le=100, description="Number of memory results to retrieve"
    )
    memory_similarity_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for memory filtering",
    )

    # --- Tool Retrieval Config ---
    tools_results_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of tools to retrieve",
    )
    tools_similarity_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for tool retrieval",
    )

    # --- Memory Tool Backend ---
    memory_tool_backend: str | None = Field(
        default=None,
        description="Backend for memory tool. Options: 'local', 's3', 'db'",
    )

    @field_validator("memory_tool_backend")
    @classmethod
    def validate_backend(cls, v):
        if v is None:
            return v
        allowed = {"local", "s3", "db"}
        if v not in allowed:
            raise ValueError(
                f"Invalid memory_tool_backend '{v}'. Must be one of {allowed}."
            )
        return v

    @field_validator("request_limit", "total_tokens_limit", mode="before")
    @classmethod
    def convert_none_to_zero(cls, v):
        return 0 if v is None else v


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
    tool_executor: Any  # ToolExecutor instance
    tool_name: str
    tool_args: dict


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
    handler: Any  # ToolExecutor instance
    tool_data: dict[str, Any]
    available_tools: dict[str, Any]


class LoopDetectorConfig(BaseModel):
    max_repeats: int = 3
    similarity_threshold: float = 0.9


class SessionState(BaseModel):
    messages: list[Message]
    state: AgentState
    loop_detector: Any  # RobustLoopDetector instance
    assistant_with_tool_calls: dict | None
    pending_tool_responses: list[dict]
