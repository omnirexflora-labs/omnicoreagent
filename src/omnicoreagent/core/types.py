from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator, model_validator
import json

from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig


class AgentConfig(BaseModel):
    agent_name: str
    request_limit: int = Field(default=0, description="0 = unlimited (production mode)")
    total_tokens_limit: int = Field(
        default=0, description="0 = unlimited (production mode)"
    )
    max_steps: int = Field(gt=0, le=1000)
    tool_call_timeout: int = Field(gt=1, le=1000)
    enable_advanced_tool_use: bool = Field(
        default=False, description="enable_advanced_tool_use"
    )

    memory_config: dict = {
        "mode": "sliding_window",
        "value": 10000,
        "summary": {
            "enabled": False,
            "retention_policy": "keep"
        }
    }

    memory_tool_backend: str | None = Field(
        default=None,
        description="Backend for memory tool. Options: 'local', 's3', 'db'",
    )

    enable_agent_skills: bool = Field(
        default=False,
        description="Enable Agent Skills feature for specialized capabilities",
    )

    context_management: dict = Field(
        default={
            "enabled": False,
            "mode": "token_budget",
            "value": 100000,
            "threshold_percent": 75,
            "strategy": "truncate",
            "preserve_recent": 4,
        },
        description="Context management config for agent loop to prevent token exhaustion",
    )

    tool_offload: dict = Field(
        default={
            "enabled": False,
            "threshold_tokens": 500,
            "threshold_bytes": 2000,
            "max_preview_tokens": 150,
            "storage_dir": ".omnicoreagent_artifacts",
        },
        description="Tool response offloading config to reduce context size from large tool outputs",
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

    @field_validator("context_management")
    @classmethod
    def validate_context_management(cls, v):
        if v is None:
            return v
        
        preserve_recent = v.get("preserve_recent", 4)
        if preserve_recent < 4:
            raise ValueError(
                f"context_management.preserve_recent must be at least 4, got {preserve_recent}"
            )
        
        allowed_modes = {"sliding_window", "token_budget"}
        mode = v.get("mode", "token_budget")
        if mode not in allowed_modes:
            raise ValueError(
                f"context_management.mode must be one of {allowed_modes}, got '{mode}'"
            )
        
        allowed_strategies = {"truncate", "summarize_and_truncate"}
        strategy = v.get("strategy", "truncate")
        if strategy not in allowed_strategies:
            raise ValueError(
                f"context_management.strategy must be one of {allowed_strategies}, got '{strategy}'"
            )
        
        threshold = v.get("threshold_percent", 75)
        if not (1 <= threshold <= 100):
            raise ValueError(
                f"context_management.threshold_percent must be between 1 and 100, got {threshold}"
            )
        
        value = v.get("value", 100000)
        if value <= 0:
            raise ValueError(
                f"context_management.value must be positive, got {value}"
            )
        
        return v

    @field_validator("tool_offload")
    @classmethod
    def validate_tool_offload(cls, v):
        if v is None:
            return v
        
        threshold_tokens = v.get("threshold_tokens", 500)
        if threshold_tokens <= 0:
            raise ValueError(
                f"tool_offload.threshold_tokens must be positive, got {threshold_tokens}"
            )
        
        threshold_bytes = v.get("threshold_bytes", 2000)
        if threshold_bytes <= 0:
            raise ValueError(
                f"tool_offload.threshold_bytes must be positive, got {threshold_bytes}"
            )
        
        max_preview_tokens = v.get("max_preview_tokens", 150)
        if max_preview_tokens <= 0:
            raise ValueError(
                f"tool_offload.max_preview_tokens must be positive, got {max_preview_tokens}"
            )
        
        max_preview_lines = v.get("max_preview_lines", 10)
        if max_preview_lines <= 0:
            raise ValueError(
                f"tool_offload.max_preview_lines must be positive, got {max_preview_lines}"
            )
        
        storage_dir = v.get("storage_dir", ".omnicoreagent_artifacts")
        if not isinstance(storage_dir, str) or not storage_dir:
            raise ValueError(
                f"tool_offload.storage_dir must be a non-empty string"
            )
        
        retention_days = v.get("retention_days")
        if retention_days is not None and retention_days < 0:
            raise ValueError(
                f"tool_offload.retention_days must be non-negative, got {retention_days}"
            )
        
        return v


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
