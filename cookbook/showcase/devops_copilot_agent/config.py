"""
Production-grade configuration management for DevOps Copilot.
Supports environment variables, config files, and validation.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
import yaml
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class DevopsConfig:
    """Devops agent configuration."""

    timeout_seconds: int = 60
    max_output_chars: int = 100_000
    enable_history: bool = True
    max_history_size: int = 500
    working_directory: str = field(default_factory=lambda: os.getcwd())

    def validate(self) -> None:
        """Validate configuration values."""
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if self.max_history_size <= 0:
            raise ValueError("max_history_size must be positive")


@dataclass
class AgentConfig:
    """
    OmniAgent configuration.

    Defines runtime behavior, limits, memory strategy, and tool retrieval settings.
    """

    # --- Identification ---
    name: str = "OmniDevOpsCopilot"  # Unique agent identifier

    # --- Execution Controls ---
    max_steps: int = 20  # Max number of reasoning/tool steps before termination
    tool_call_timeout: int = 30  # Timeout for each tool call (seconds)

    # --- Limits ---
    request_limit: int = 0  # 0 = unlimited (production mode)
    total_tokens_limit: int = 0  # 0 = unlimited (production mode)

    # --- Memory Retrieval Config ---
    memory_mode: str = "sliding_window"  # Options: sliding_window, token_budget
    memory_window_size: int = 100  # Used if mode == "sliding_window"
    memory_results_limit: int = 5  # Number of memory results to retrieve (1–100)
    memory_similarity_threshold: float = (
        0.5  # Similarity threshold for memory filtering (0.0–1.0)
    )

    # --- Tool Retrieval Config ---
    enable_tools_knowledge_base: bool = False  # Enable semantic tool retrieval
    tools_results_limit: int = 10  # Max number of tools to retrieve (1–100)
    tools_similarity_threshold: float = (
        0.1  # Similarity threshold for tool retrieval (0.0–1.0)
    )

    # --- Memory Tool Backend ---
    memory_tool_backend: Optional[str] = (
        None  # "local"  Enable the agent to write to file during process
    )

    # --- Validation ---
    def validate(self) -> None:
        """Validate configuration values."""
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.tool_call_timeout <= 0:
            raise ValueError("tool_call_timeout must be positive")
        if not (0.0 <= self.memory_similarity_threshold <= 1.0):
            raise ValueError("memory_similarity_threshold must be between 0.0 and 1.0")
        if not (0.0 <= self.tools_similarity_threshold <= 1.0):
            raise ValueError("tools_similarity_threshold must be between 0.0 and 1.0")
        if self.memory_results_limit < 1 or self.memory_results_limit > 100:
            raise ValueError("memory_results_limit must be between 1 and 100")
        if self.tools_results_limit < 1 or self.tools_results_limit > 100:
            raise ValueError("tools_results_limit must be between 1 and 100")
        if self.memory_mode not in {"sliding_window", "token_budget"}:
            raise ValueError(f"Invalid memory_mode: {self.memory_mode}")


@dataclass
class ModelConfig:
    """LLM model configuration."""

    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.0
    top_p: float = 0.0
    max_tokens: Optional[int] = None
    max_context_length: int = 2000
    llm_api_key: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        """Load API key from environment if not provided."""
        if not self.llm_api_key:
            self.llm_api_key = os.getenv("LLM_API_KEY")

    def validate(self) -> None:
        """Validate configuration values."""

        if not self.llm_api_key:
            raise ValueError(f"LLM API key required for {self.provider}")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")


@dataclass
class StorageConfig:
    """Memory and event storage configuration."""

    memory_store_type: str = "redis"
    event_store_type: str = "redis_stream"
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10
    redis_socket_timeout: int = 5
    redis_retry_on_timeout: bool = True

    def validate(self) -> None:
        """Validate configuration values."""
        if self.memory_store_type not in {"redis", "in_memory"}:
            raise ValueError(f"Invalid memory_store_type: {self.memory_store_type}")
        if self.event_store_type not in {"redis_stream", "in_memory"}:
            raise ValueError(f"Invalid event_store_type: {self.event_store_type}")


@dataclass
class SecurityConfig:
    """Security and rate limiting configuration."""

    enable_rate_limiting: bool = True
    rate_limit_requests: int = 100  # per hour
    rate_limit_window: int = 3600  # seconds
    enable_audit_logging: bool = True
    audit_log_file: str = "audit.log"
    max_command_length: int = 10_000
    blocked_patterns: list = field(
        default_factory=lambda: [
            r".*\$\(.*rm.*\).*",  # Command substitution with rm
            r".*;\s*rm\s+.*",  # Chained rm commands
            r".*&&\s*sudo.*",  # Chained sudo
        ]
    )

    def validate(self) -> None:
        """Validate configuration values."""
        if self.rate_limit_requests <= 0:
            raise ValueError("rate_limit_requests must be positive")
        if self.max_command_length <= 0:
            raise ValueError("max_command_length must be positive")


@dataclass
class ObservabilityConfig:
    """Observability and monitoring configuration."""

    enable_metrics: bool = True
    metrics_port: int = 9090
    enable_tracing: bool = False
    tracing_endpoint: Optional[str] = None
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: str = "copilot.log"
    log_max_bytes: int = 10_485_760  # 10MB
    log_backup_count: int = 5

    def validate(self) -> None:
        """Validate configuration values."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise ValueError(f"Invalid log_level: {self.log_level}")
        if self.log_format not in {"json", "text"}:
            raise ValueError(f"Invalid log_format: {self.log_format}")


@dataclass
class ProductionConfig:
    devops: DevopsConfig = field(default_factory=DevopsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    # ------------------------------------------------------------------
    # 1. Load YAML (defaults)
    # ------------------------------------------------------------------
    @classmethod
    def _from_yaml(cls, path: Path) -> "ProductionConfig":
        if not path.exists():
            logger.warning("config.yaml not found – using built-in defaults")
            return cls()
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return cls(
            devops=DevopsConfig(**data.get("devops", {})),
            agent=AgentConfig(**data.get("agent", {})),
            model=ModelConfig(**data.get("model", {})),
            storage=StorageConfig(**data.get("storage", {})),
            security=SecurityConfig(**data.get("security", {})),
            observability=ObservabilityConfig(**data.get("observability", {})),
        )

    # ------------------------------------------------------------------
    # 2. Override with env vars (same mapping you already had)
    # ------------------------------------------------------------------
    @classmethod
    def _apply_env_overrides(cls, cfg: "ProductionConfig") -> "ProductionConfig":
        # Helper to get int/bool/float safely
        def _int(key, default):
            return int(os.getenv(key, default))

        def _bool(key, default):
            return os.getenv(key, str(default)).lower() == "true"

        def _float(key, default):
            return float(os.getenv(key, default))

        # ---- devops ----------------------------------------------------
        cfg.devops.timeout_seconds = _int("DEVOPS_TIMEOUT", cfg.devops.timeout_seconds)
        cfg.devops.max_output_chars = _int(
            "DEVOPS_MAX_OUTPUT", cfg.devops.max_output_chars
        )
        cfg.devops.enable_history = _bool(
            "DEVOPS_ENABLE_HISTORY", cfg.devops.enable_history
        )
        cfg.devops.max_history_size = _int(
            "DEVOPS_HISTORY_SIZE", cfg.devops.max_history_size
        )
        cfg.devops.working_directory = os.getenv(
            "DEVOPS_WORKING_DIR", cfg.devops.working_directory
        )

        # ---- agent -----------------------------------------------------
        cfg.agent.name = os.getenv("AGENT_NAME", cfg.agent.name)
        cfg.agent.max_steps = _int("AGENT_MAX_STEPS", cfg.agent.max_steps)
        cfg.agent.tool_call_timeout = _int(
            "AGENT_TOOL_TIMEOUT", cfg.agent.tool_call_timeout
        )
        cfg.agent.memory_mode = os.getenv("AGENT_MEMORY_MODE", cfg.agent.memory_mode)
        cfg.agent.memory_window_size = _int(
            "AGENT_MEMORY_WINDOW", cfg.agent.memory_window_size
        )
        cfg.agent.request_limit = _int("AGENT_REQUEST_LIMIT", cfg.agent.request_limit)
        cfg.agent.total_tokens_limit = _int(
            "AGENT_TOKEN_LIMIT", cfg.agent.total_tokens_limit
        )

        # --- Memory Retrieval Config ---
        cfg.agent.memory_results_limit = _int(
            "AGENT_MEMORY_RESULTS_LIMIT", cfg.agent.memory_results_limit
        )
        cfg.agent.memory_similarity_threshold = _float(
            "AGENT_MEMORY_SIMILARITY_THRESHOLD", cfg.agent.memory_similarity_threshold
        )

        # --- Tool Retrieval Config ---
        cfg.agent.enable_tools_knowledge_base = _bool(
            "AGENT_ENABLE_TOOLS_KB", cfg.agent.enable_tools_knowledge_base
        )
        cfg.agent.tools_results_limit = _int(
            "AGENT_TOOLS_RESULTS_LIMIT", cfg.agent.tools_results_limit
        )
        cfg.agent.tools_similarity_threshold = _float(
            "AGENT_TOOLS_SIMILARITY_THRESHOLD", cfg.agent.tools_similarity_threshold
        )

        # --- Memory Tool Backend ---
        cfg.agent.memory_tool_backend = os.getenv(
            "AGENT_MEMORY_TOOL_BACKEND", cfg.agent.memory_tool_backend
        )

        # ---- model -----------------------------------------------------
        cfg.model.provider = os.getenv("MODEL_PROVIDER", cfg.model.provider)
        cfg.model.model = os.getenv("MODEL_NAME", cfg.model.model)
        cfg.model.temperature = _float("MODEL_TEMPERATURE", cfg.model.temperature)
        cfg.model.top_p = _float("MODEL_TOP_P", cfg.model.top_p)
        cfg.model.max_context_length = _int(
            "MODEL_MAX_CONTEXT", cfg.model.max_context_length
        )
        cfg.model.llm_api_key = os.getenv("LLM_API_KEY") or cfg.model.llm_api_key

        # ---- storage ---------------------------------------------------
        cfg.storage.memory_store_type = os.getenv(
            "MEMORY_STORE_TYPE", cfg.storage.memory_store_type
        )
        cfg.storage.event_store_type = os.getenv(
            "EVENT_STORE_TYPE", cfg.storage.event_store_type
        )
        cfg.storage.redis_url = os.getenv("REDIS_URL", cfg.storage.redis_url)
        cfg.storage.redis_max_connections = _int(
            "REDIS_MAX_CONN", cfg.storage.redis_max_connections
        )

        # ---- security --------------------------------------------------
        cfg.security.enable_rate_limiting = _bool(
            "SECURITY_RATE_LIMIT", cfg.security.enable_rate_limiting
        )
        cfg.security.rate_limit_requests = _int(
            "SECURITY_RATE_LIMIT_REQUESTS", cfg.security.rate_limit_requests
        )
        cfg.security.enable_audit_logging = _bool(
            "SECURITY_AUDIT_LOG", cfg.security.enable_audit_logging
        )

        # ---- observability ---------------------------------------------
        cfg.observability.enable_metrics = _bool(
            "OBSERVABILITY_METRICS", cfg.observability.enable_metrics
        )
        cfg.observability.log_level = os.getenv(
            "LOG_LEVEL", cfg.observability.log_level
        )
        cfg.observability.log_format = os.getenv(
            "LOG_FORMAT", cfg.observability.log_format
        )
        cfg.observability.log_file = os.getenv(
            "OBSERVABILITY_LOG_FILE", cfg.observability.log_file
        )
        cfg.security.audit_log_file = os.getenv(
            "SECURITY_AUDIT_LOG_FILE", cfg.security.audit_log_file
        )
        cfg.observability.metrics_port = _int(
            "OBSERVABILITY_METRICS_PORT", cfg.observability.metrics_port
        )

        return cfg

    # ------------------------------------------------------------------
    # Public factory – **single entry point**
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, yaml_path: str = "config.yaml") -> "ProductionConfig":
        cfg = cls._from_yaml(Path(yaml_path))
        cfg = cls._apply_env_overrides(cfg)
        cfg.validate()
        logger.info("Configuration loaded & validated")
        return cfg

    # ------------------------------------------------------------------
    # Validation (unchanged)
    # ------------------------------------------------------------------
    def validate(self) -> None:
        self.devops.validate()
        self.agent.validate()
        self.model.validate()
        self.storage.validate()
        self.security.validate()
        self.observability.validate()
