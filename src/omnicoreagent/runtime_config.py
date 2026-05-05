from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from omnicoreagent.core.constants import SUPPORTED_MODELS_PROVIDERS


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


@dataclass
class ModelConfig:
    provider: str
    model: str
    temperature: float | None = 0.5
    max_tokens: int | None = 5000
    max_context_length: int | None = 100000
    top_p: float | None = 0.7
    top_k: int | str | None = "N/A"
    api_key: str | None = None
    azure_endpoint: str | None = None
    azure_api_version: str | None = None
    azure_deployment: str | None = None
    ollama_host: str | None = None


@dataclass
class MCPToolConfig:
    name: str | None = None
    transport_type: TransportType | str = TransportType.STDIO
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    timeout: int | None = 60
    sse_read_timeout: int | None = 120
    auth: dict[str, Any] | None = None

    def __post_init__(self):
        self.transport_type = TransportType(self.transport_type)
        if not self.name:
            base = self.command or self.url or "mcp_tool"
            self.name = f"{base}_{uuid.uuid4().hex[:6]}"


class AgentConfig(BaseModel):
    agent_name: str = "OmniCoreAgent"
    request_limit: int = Field(default=0, description="0 = unlimited")
    total_tokens_limit: int = Field(default=0, description="0 = unlimited")
    max_steps: int = Field(default=15, gt=0, le=1000)
    tool_call_timeout: int = Field(default=30, gt=1, le=1000)
    enable_advanced_tool_use: bool = False
    enable_subagents: bool = False
    enable_agent_skills: bool = False
    memory_config: dict[str, Any] = Field(
        default_factory=lambda: {
            "mode": "sliding_window",
            "value": 10000,
            "summary": {"enabled": False, "retention_policy": "keep"},
        }
    )
    enable_workspace_memory: bool = False
    guardrail_config: dict[str, Any] = Field(default_factory=dict)
    guardrail_mode: str = "full"
    context_management: dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": False,
            "mode": "token_budget",
            "value": 100000,
            "threshold_percent": 75,
            "strategy": "truncate",
            "preserve_recent": 4,
        }
    )
    tool_offload: dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": False,
            "threshold_tokens": 500,
            "threshold_bytes": 2000,
            "max_preview_tokens": 150,
            "max_preview_lines": 10,
        }
    )

    @field_validator("request_limit", "total_tokens_limit", mode="before")
    @classmethod
    def convert_none_to_zero(cls, value):
        return 0 if value is None else value

    @field_validator("guardrail_config", mode="before")
    @classmethod
    def default_guardrail_config(cls, value):
        return {} if value is None else value

    @field_validator("context_management")
    @classmethod
    def validate_context_management(cls, value):
        if value is None:
            return {}

        preserve_recent = value.get("preserve_recent", 4)
        if preserve_recent < 4:
            raise ValueError(
                f"context_management.preserve_recent must be at least 4, got {preserve_recent}"
            )

        allowed_modes = {"sliding_window", "token_budget"}
        mode = value.get("mode", "token_budget")
        if mode not in allowed_modes:
            raise ValueError(
                f"context_management.mode must be one of {allowed_modes}, got '{mode}'"
            )

        allowed_strategies = {"truncate", "summarize_and_truncate"}
        strategy = value.get("strategy", "truncate")
        if strategy not in allowed_strategies:
            raise ValueError(
                f"context_management.strategy must be one of {allowed_strategies}, got '{strategy}'"
            )

        threshold = value.get("threshold_percent", 75)
        if not (1 <= threshold <= 100):
            raise ValueError(
                f"context_management.threshold_percent must be between 1 and 100, got {threshold}"
            )

        context_value = value.get("value", 100000)
        if context_value <= 0:
            raise ValueError(
                f"context_management.value must be positive, got {context_value}"
            )

        return value

    @field_validator("tool_offload")
    @classmethod
    def validate_tool_offload(cls, value):
        if value is None:
            return {}

        threshold_tokens = value.get("threshold_tokens", 500)
        if threshold_tokens <= 0:
            raise ValueError(
                f"tool_offload.threshold_tokens must be positive, got {threshold_tokens}"
            )

        threshold_bytes = value.get("threshold_bytes", 2000)
        if threshold_bytes <= 0:
            raise ValueError(
                f"tool_offload.threshold_bytes must be positive, got {threshold_bytes}"
            )

        max_preview_tokens = value.get("max_preview_tokens", 150)
        if max_preview_tokens <= 0:
            raise ValueError(
                f"tool_offload.max_preview_tokens must be positive, got {max_preview_tokens}"
            )

        max_preview_lines = value.get("max_preview_lines", 10)
        if max_preview_lines <= 0:
            raise ValueError(
                f"tool_offload.max_preview_lines must be positive, got {max_preview_lines}"
            )

        retention_days = value.get("retention_days")
        if retention_days is not None and retention_days < 0:
            raise ValueError(
                f"tool_offload.retention_days must be non-negative, got {retention_days}"
            )

        return value

    @model_validator(mode="after")
    def enable_required_harness_defaults(self):
        if self.enable_subagents:
            self.enable_workspace_memory = True
        return self


def normalize_model_config(config: dict[str, Any] | ModelConfig) -> dict[str, Any]:
    if isinstance(config, ModelConfig):
        data = asdict(config)
    elif isinstance(config, dict):
        data = dict(config)
    else:
        raise ValueError("model_config must be a dict or ModelConfig")

    provider = data.get("provider")
    model = data.get("model")
    if not provider:
        raise ValueError("model_config.provider is required")
    if provider not in SUPPORTED_MODELS_PROVIDERS:
        supported = ", ".join(SUPPORTED_MODELS_PROVIDERS)
        raise ValueError(f"Unsupported provider: {provider}. Supported: {supported}")
    if not model:
        raise ValueError("model_config.model is required")

    data["provider"] = SUPPORTED_MODELS_PROVIDERS[provider]
    return data


def normalize_mcp_tool_config(config: dict[str, Any] | MCPToolConfig) -> dict[str, Any]:
    tool = config if isinstance(config, MCPToolConfig) else MCPToolConfig(**config)
    data = asdict(tool)
    data["transport_type"] = tool.transport_type.value

    if tool.transport_type in {TransportType.SSE, TransportType.STREAMABLE_HTTP}:
        if not tool.url:
            raise ValueError(f"url is required for {tool.transport_type.value} transport")
    elif tool.transport_type == TransportType.STDIO and not tool.command:
        raise ValueError("command is required for stdio transport")

    return {key: value for key, value in data.items() if value is not None}


def normalize_mcp_tools(
    tools: list[dict[str, Any] | MCPToolConfig] | None,
) -> list[dict[str, Any]]:
    normalized = [normalize_mcp_tool_config(tool) for tool in tools or []]
    names = [tool["name"] for tool in normalized]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ValueError(f"Duplicate MCP tool names: {', '.join(sorted(duplicates))}")
    return normalized


def normalize_agent_config(
    name: str, config: dict[str, Any] | AgentConfig | None = None
) -> dict[str, Any]:
    if isinstance(config, AgentConfig):
        data = config.model_copy(update={"agent_name": name}).model_dump()
    elif isinstance(config, dict):
        data = AgentConfig(**{**config, "agent_name": name}).model_dump()
    elif config is None:
        data = AgentConfig(agent_name=name).model_dump()
    else:
        raise ValueError("agent_config must be a dict or AgentConfig")
    return data
