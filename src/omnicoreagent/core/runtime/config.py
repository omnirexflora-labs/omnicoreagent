from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from enum import Enum
from os import PathLike
from typing import Any
import uuid

from omnicoreagent.core.workspace.config import (
    WorkspaceConfig,
    resolve_workspace_config,
)


SUPPORTED_MODELS_PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "groq": "groq",
    "ollama": "ollama",
    "azure": "azure",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "openrouter": "openrouter",
    "cencori": "cencori",
}


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


def _default_governance_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "profile": "interactive-dev",
        "policy": None,
        "policy_path": None,
        "project_root": None,
        "approval_resolver": None,
        "sandbox_runtime": None,
        "sandbox_config": None,
        "allow_test_sandbox_runtime": False,
        "allow_static_high_risk_approvals": False,
    }


GOVERNANCE_CONFIG_KEYS = frozenset(_default_governance_config())


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
    enable_workspace_files: bool = True
    guardrail_config: dict[str, Any] = field(default_factory=dict)
    guardrail_mode: str = "full"
    context_management: dict[str, Any] = field(
        default_factory=_default_context_management
    )
    tool_offload: dict[str, Any] = field(default_factory=_default_tool_offload)
    governance_config: dict[str, Any] = field(default_factory=_default_governance_config)
    workspace_config: WorkspaceConfig | dict[str, Any] | None = None

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
        if not isinstance(self.governance_config, dict):
            raise ValueError("governance_config must be a dict")
        _validate_unknown_keys(
            "governance_config",
            self.governance_config,
            GOVERNANCE_CONFIG_KEYS,
        )
        self.governance_config = _merge_defaults(
            _default_governance_config(), self.governance_config
        )
        if self.workspace_config is not None:
            self.workspace_config = resolve_workspace_config(self.workspace_config)

        _validate_range("max_steps", self.max_steps, minimum=1, maximum=1000)
        _validate_range(
            "tool_call_timeout", self.tool_call_timeout, minimum=2, maximum=1000
        )
        _validate_context_management(self.context_management)
        _validate_tool_offload(self.tool_offload)
        _validate_governance_config(self.governance_config)

        if self.enable_subagents:
            self.enable_workspace_files = True

    def model_dump(self) -> dict[str, Any]:
        data = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "governance_config":
                data[item.name] = dict(value)
            elif is_dataclass(value):
                data[item.name] = asdict(value)
            else:
                data[item.name] = value
        return data

    def model_copy(self, *, update: dict[str, Any] | None = None) -> AgentConfig:
        return replace(self, **(update or {}))


def default_agent_config(name: str) -> dict[str, Any]:
    return AgentConfig(agent_name=name).model_dump()


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


def _merge_defaults(defaults: dict[str, Any], value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return defaults
    return {**defaults, **value}


def _validate_unknown_keys(
    name: str,
    value: dict[str, Any],
    allowed_keys: frozenset[str],
) -> None:
    unknown = set(value) - allowed_keys
    if unknown:
        allowed = ", ".join(sorted(allowed_keys))
        found = ", ".join(sorted(unknown))
        raise ValueError(f"{name} has unknown keys: {found}. Allowed keys: {allowed}")


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


def _validate_governance_config(value: dict[str, Any]):
    if not isinstance(value, dict):
        raise ValueError("governance_config must be a dict")
    if not isinstance(value.get("enabled", False), bool):
        raise ValueError("governance_config.enabled must be a boolean")
    if not isinstance(value.get("allow_static_high_risk_approvals", False), bool):
        raise ValueError(
            "governance_config.allow_static_high_risk_approvals must be a boolean"
        )
    if not isinstance(value.get("allow_test_sandbox_runtime", False), bool):
        raise ValueError("governance_config.allow_test_sandbox_runtime must be a boolean")
    sandbox_config = value.get("sandbox_config")
    if sandbox_config is not None:
        if value.get("sandbox_runtime") is not None:
            raise ValueError(
                "governance_config cannot set both sandbox_runtime and sandbox_config"
            )
        if isinstance(sandbox_config, str):
            provider = sandbox_config
        elif isinstance(sandbox_config, dict):
            _validate_unknown_keys(
                "governance_config.sandbox_config",
                sandbox_config,
                frozenset({"provider"}),
            )
            provider = sandbox_config.get("provider", "none")
        else:
            try:
                from omnicoreagent.sandbox import SandboxRuntimeConfig

                valid_config = isinstance(sandbox_config, SandboxRuntimeConfig)
                provider = sandbox_config.provider.value if valid_config else None
            except Exception:
                valid_config = False
                provider = None
            if not valid_config:
                raise ValueError("governance_config.sandbox_config must be a dict or string")
        if provider not in {"none", "local_test"}:
            raise ValueError(
                "governance_config.sandbox_config.provider must be one of "
                "{'none', 'local_test'}"
            )
    profile = value.get("profile", "interactive-dev")
    if profile not in {"permissive-dev", "interactive-dev", "strict-production"}:
        raise ValueError(
            "governance_config.profile must be one of "
            "{'permissive-dev', 'interactive-dev', 'strict-production'}"
        )
    if value.get("policy") is not None and value.get("policy_path") is not None:
        raise ValueError("governance_config cannot set both policy and policy_path")
    policy = value.get("policy")
    if policy is not None and not isinstance(policy, dict):
        try:
            from omnicoreagent.governance import PolicyEnvelope

            valid_policy = isinstance(policy, PolicyEnvelope)
        except Exception:
            valid_policy = False
        if not valid_policy:
            raise ValueError("governance_config.policy must be a dict or PolicyEnvelope")
    for key in ("policy_path", "project_root"):
        path_value = value.get(key)
        if path_value is not None and not isinstance(path_value, (str, PathLike)):
            raise ValueError(f"governance_config.{key} must be a string or path-like")
