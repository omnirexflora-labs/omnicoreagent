from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
import uuid


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


def default_agent_config(name: str) -> dict[str, Any]:
    return {
        "agent_name": name,
        "request_limit": 0,
        "total_tokens_limit": 0,
        "max_steps": 15,
        "tool_call_timeout": 30,
        "enable_advanced_tool_use": False,
        "enable_subagents": False,
        "enable_agent_skills": False,
        "memory_config": {
            "mode": "sliding_window",
            "value": 10000,
            "summary": {"enabled": False, "retention_policy": "keep"},
        },
        "enable_workspace_memory": False,
        "guardrail_config": {},
        "guardrail_mode": "full",
        "context_management": {
            "enabled": False,
            "mode": "token_budget",
            "value": 100000,
            "threshold_percent": 75,
            "strategy": "truncate",
            "preserve_recent": 4,
        },
        "tool_offload": {
            "enabled": False,
            "threshold_tokens": 500,
            "threshold_bytes": 2000,
            "max_preview_tokens": 150,
            "max_preview_lines": 10,
        },
    }


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


def normalize_agent_config_light(name: str, config: Any = None) -> dict[str, Any]:
    if hasattr(config, "model_copy") and hasattr(config, "model_dump"):
        data = config.model_copy(update={"agent_name": name}).model_dump()
    elif isinstance(config, dict):
        data = {**default_agent_config(name), **config, "agent_name": name}
    elif config is None:
        data = default_agent_config(name)
    else:
        raise ValueError("agent_config must be a dict or AgentConfig")

    if data.get("request_limit") is None:
        data["request_limit"] = 0
    if data.get("total_tokens_limit") is None:
        data["total_tokens_limit"] = 0
    if data.get("guardrail_config") is None:
        data["guardrail_config"] = {}
    if data.get("enable_subagents"):
        data["enable_workspace_memory"] = True

    return data
