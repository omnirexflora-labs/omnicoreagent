import json
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict, field
from enum import Enum
import uuid
from omnicoreagent.core.utils import logger
from omnicoreagent.core.constants import SUPPORTED_MODELS_PROVIDERS
from decouple import config


class TransportType(str, Enum):
    """Supported transport types for MCP tools"""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


@dataclass
class ModelConfig:
    """User-friendly model configuration"""

    provider: str
    model: str

    temperature: Optional[float] = 0.5
    max_tokens: Optional[int] = 5000
    max_context_length: Optional[int] = 100000
    top_p: Optional[float] = 0.7
    top_k: Optional[Union[int, str]] = "N/A"


@dataclass
class MCPToolConfig:
    """User-friendly MCP tool configuration"""

    name: Optional[str] = None
    transport_type: TransportType = TransportType.STDIO
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    headers: Optional[Dict[str, str]] = None
    env: Optional[Dict[str, str]] = None
    timeout: Optional[int] = 60
    sse_read_timeout: Optional[int] = 120
    auth: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if not self.name:
            base = self.command or self.url or "mcp_tool"
            self.name = f"{base}_{uuid.uuid4().hex[:6]}"


@dataclass
class AgentConfig:
    """User-friendly agent configuration"""

    agent_name: str = "OmniCoreAgent"
    tool_call_timeout: int = 30
    max_steps: int = 15
    request_limit: int = 0
    total_tokens_limit: int = 0
    enable_advanced_tool_use: bool = False
    enable_agent_skills: bool = False
    memory_config: dict = field(
        default_factory=lambda: {"mode": "token_budget", "value": 30000}
    )
    memory_tool_backend: str = None
    guardrail_config: Optional[Dict[str, Any]] = None


class ConfigTransformer:
    """Transforms user-friendly configuration to internal format"""

    def __init__(self):
        self.supported_providers = SUPPORTED_MODELS_PROVIDERS

        self.supported_transports = {
            TransportType.STDIO: self._transform_stdio_config,
            TransportType.SSE: self._transform_sse_config,
            TransportType.STREAMABLE_HTTP: self._transform_streamable_http_config,
        }

    def transform_config(
        self,
        model_config: Union[Dict[str, Any], ModelConfig],
        mcp_tools: List[Union[Dict[str, Any], MCPToolConfig]],
        agent_config: Optional[Union[Dict[str, Any], AgentConfig]] = None,
    ) -> Dict[str, Any]:
        """
        Transform user configuration to internal format

        Args:
            model_config: Model configuration (dict or ModelConfig)
            mcp_tools: List of MCP tool configurations
            agent_config: Optional agent configuration

        Returns:
            Internal configuration dictionary
        """
        try:
            model = self._ensure_model_config(model_config)
            tools = [self._ensure_tool_config(tool) for tool in mcp_tools]
            agent = (
                self._ensure_agent_config(agent_config)
                if agent_config
                else AgentConfig()
            )

            self._validate_model_config(model)
            self._validate_tools_config(tools)

            internal_config = {
                "AgentConfig": asdict(agent),
                "LLM": self._transform_model_config(model),
                "mcpServers": self._transform_tools_config(tools),
            }

            logger.info(
                f"Successfully transformed configuration for {len(tools)} MCP tools"
            )
            return internal_config

        except Exception as e:
            logger.error(f"Configuration transformation failed: {e}")
            raise ValueError(f"Invalid configuration: {e}")

    def _ensure_model_config(
        self, config: Union[Dict[str, Any], ModelConfig]
    ) -> ModelConfig:
        """Ensure model config is a ModelConfig instance"""
        if isinstance(config, dict):
            return ModelConfig(**config)
        elif isinstance(config, ModelConfig):
            return config
        else:
            raise ValueError("model_config must be dict or ModelConfig")

    def _ensure_tool_config(
        self, config: Union[Dict[str, Any], MCPToolConfig]
    ) -> MCPToolConfig:
        """Ensure tool config is an MCPToolConfig instance"""
        if isinstance(config, dict):
            return MCPToolConfig(**config)
        elif isinstance(config, MCPToolConfig):
            return config
        else:
            raise ValueError("mcp_tools must contain dict or MCPToolConfig")

    def _ensure_agent_config(
        self, config: Union[Dict[str, Any], AgentConfig]
    ) -> AgentConfig:
        """Ensure agent config is an AgentConfig instance"""
        if isinstance(config, dict):
            filtered_config = config.copy()
            if filtered_config.get("request_limit") is None:
                filtered_config.pop("request_limit", None)
            if filtered_config.get("total_tokens_limit") is None:
                filtered_config.pop("total_tokens_limit", None)
            return AgentConfig(**filtered_config)
        elif isinstance(config, AgentConfig):
            return config
        else:
            raise ValueError("agent_config must be dict or AgentConfig")

    def _validate_model_config(self, config: ModelConfig):
        """Validate model configuration"""
        if not config.provider:
            raise ValueError("Model provider is required")

        if config.provider not in self.supported_providers:
            supported = ", ".join(self.supported_providers.keys())
            raise ValueError(
                f"Unsupported provider: {config.provider}. Supported: {supported}"
            )

        if not config.model:
            raise ValueError("Model name is required")

        if config.temperature is not None and not (0 <= config.temperature <= 2):
            raise ValueError("Temperature must be between 0 and 2")

        if config.max_tokens is not None and config.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        if config.max_context_length is not None and config.max_context_length <= 0:
            raise ValueError("max_context_length must be positive")

    def _validate_tools_config(self, tools: List[MCPToolConfig]):
        """Validate MCP tools configuration"""
        if not tools:
            logger.warning("No MCP tools provided, using local tools only")
            return

        tool_names = set()
        for tool in tools:
            if not tool.name:
                raise ValueError("Tool name is required")

            if tool.name in tool_names:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            tool_names.add(tool.name)

            if tool.transport_type not in self.supported_transports:
                supported = ", ".join([t.value for t in TransportType])
                raise ValueError(
                    f"Unsupported transport type: {tool.transport_type}. Supported: {supported}"
                )

            self._validate_tool_transport(tool)

    def _validate_tool_transport(self, tool: MCPToolConfig):
        """Validate tool transport configuration"""
        if tool.transport_type in [TransportType.SSE, TransportType.STREAMABLE_HTTP]:
            if not tool.url:
                raise ValueError(f"URL is required for {tool.transport_type} transport")

        elif tool.transport_type == TransportType.STDIO:
            if not tool.command:
                raise ValueError("Command is required for stdio transport")

    def _transform_model_config(self, config: ModelConfig) -> Dict[str, Any]:
        """Transform model config to internal LLM format"""
        return {
            "provider": self.supported_providers[config.provider],
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "max_context_length": config.max_context_length,
            "top_p": config.top_p,
            "top_k": config.top_k,
        }

    def _transform_tools_config(self, tools: List[MCPToolConfig]) -> Dict[str, Any]:
        """Transform tools config to internal mcpServers format"""
        servers = {}

        for tool in tools:
            transformer = self.supported_transports[tool.transport_type]
            servers[tool.name] = transformer(tool)

        return servers

    def _transform_stdio_config(self, tool: MCPToolConfig) -> Dict[str, Any]:
        """Transform stdio transport configuration"""
        config = {
            "transport_type": "stdio",
            "command": tool.command,
            "args": tool.args or [],
        }

        if tool.env:
            config["env"] = tool.env

        return config

    def _transform_sse_config(self, tool: MCPToolConfig) -> Dict[str, Any]:
        """Transform SSE transport configuration"""
        config = {
            "transport_type": "sse",
            "url": tool.url,
            "timeout": tool.timeout,
            "sse_read_timeout": tool.sse_read_timeout,
        }

        if tool.headers:
            if not isinstance(tool.headers, dict):
                raise ValueError("headers must be a dictionary")
            config["headers"] = tool.headers

        return config

    def _transform_streamable_http_config(self, tool: MCPToolConfig) -> Dict[str, Any]:
        """Transform streamable HTTP transport configuration"""
        config = {
            "transport_type": "streamable_http",
            "url": tool.url,
            "timeout": tool.timeout,
        }

        if tool.headers:
            if not isinstance(tool.headers, dict):
                raise ValueError("headers must be a dictionary")
            config["headers"] = tool.headers

        if tool.auth:
            if not isinstance(tool.auth, dict):
                raise ValueError("auth must be a dictionary")
            config["auth"] = tool.auth

        return config

    def save_config(self, config: Dict[str, Any], filepath: str):
        """Save configuration to file"""
        try:
            with open(filepath, "w") as f:
                json.dump(config, f, indent=2)
            logger.info(f"Configuration saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            raise


config_transformer = ConfigTransformer()
