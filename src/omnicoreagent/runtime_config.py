from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from omnicoreagent.config_types import (
    MCPToolConfig as MCPToolConfig,
    ModelConfig as ModelConfig,
    TransportType as TransportType,
    normalize_mcp_tool_config as normalize_mcp_tool_config,
    normalize_mcp_tools as normalize_mcp_tools,
    normalize_model_config as normalize_model_config,
)


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
