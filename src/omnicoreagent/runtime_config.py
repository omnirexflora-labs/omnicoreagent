from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from omnicoreagent.config_types import (
    MCPToolConfig as MCPToolConfig,
    ModelConfig as ModelConfig,
    TransportType as TransportType,
    normalize_mcp_tool_config as normalize_mcp_tool_config,
    normalize_mcp_tools as normalize_mcp_tools,
    normalize_model_config as normalize_model_config,
)


def _default_memory_config() -> dict[str, Any]:
    return {
        "mode": "sliding_window",
        "value": 10000,
        "summary": {"enabled": False, "retention_policy": "keep"},
    }


def _default_context_management() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "token_budget",
        "value": 100000,
        "threshold_percent": 75,
        "strategy": "truncate",
        "preserve_recent": 4,
    }


def _default_tool_offload() -> dict[str, Any]:
    return {
        "enabled": False,
        "threshold_tokens": 500,
        "threshold_bytes": 2000,
        "max_preview_tokens": 150,
        "max_preview_lines": 10,
    }


@dataclass
class AgentConfig:
    agent_name: str = "OmniCoreAgent"
    request_limit: int = 0
    total_tokens_limit: int = 0
    max_steps: int = 15
    tool_call_timeout: int = 30
    mcp_enabled: bool = False
    enable_advanced_tool_use: bool = False
    enable_subagents: bool = False
    enable_agent_skills: bool = False
    memory_config: dict[str, Any] = field(default_factory=_default_memory_config)
    enable_workspace_memory: bool = False
    guardrail_config: dict[str, Any] = field(default_factory=dict)
    guardrail_mode: str = "full"
    context_management: dict[str, Any] = field(
        default_factory=_default_context_management
    )
    tool_offload: dict[str, Any] = field(default_factory=_default_tool_offload)

    def __post_init__(self):
        self.request_limit = 0 if self.request_limit is None else self.request_limit
        self.total_tokens_limit = (
            0 if self.total_tokens_limit is None else self.total_tokens_limit
        )
        self.guardrail_config = self.guardrail_config or {}
        self.memory_config = self.memory_config or _default_memory_config()
        self.context_management = _merge_defaults(
            _default_context_management(), self.context_management
        )
        self.tool_offload = _merge_defaults(_default_tool_offload(), self.tool_offload)

        _validate_range("max_steps", self.max_steps, minimum=1, maximum=1000)
        _validate_range("tool_call_timeout", self.tool_call_timeout, minimum=2, maximum=1000)
        _validate_context_management(self.context_management)
        _validate_tool_offload(self.tool_offload)

        if self.enable_subagents:
            self.enable_workspace_memory = True

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)

    def model_copy(self, *, update: dict[str, Any] | None = None) -> AgentConfig:
        return replace(self, **(update or {}))


def _merge_defaults(defaults: dict[str, Any], value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return defaults
    return {**defaults, **value}


def _validate_range(name: str, value: int, *, minimum: int, maximum: int):
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")


def _validate_context_management(value: dict[str, Any]):
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


def _validate_tool_offload(value: dict[str, Any]):
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
