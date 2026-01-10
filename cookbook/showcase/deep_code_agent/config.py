import os
from pathlib import Path
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    name: str = "DeepCoder"
    max_steps: int = 30
    tool_call_timeout: int = 80
    request_limit: int = 0
    total_tokens_limit: int = 0
    memory_mode: Literal["sliding_window", "token_budget"] = "sliding_window"
    memory_window_size: int = 100
    memory_tool_backend: Optional[str] = "local"

    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="forbid")


class ModelConfig(BaseSettings):
    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.2
    top_p: float = 0.95
    max_context_length: int = 30_000
    llm_api_key: Optional[str] = None

    @field_validator("llm_api_key", mode="before")
    def set_api_key(cls, v):
        return v or os.getenv("LLM_API_KEY")

    @field_validator("temperature")
    def temperature_range(cls, v):
        if not (0 <= v <= 2):
            raise ValueError("temperature must be between 0 and 2")
        return v

    model_config = SettingsConfigDict(env_prefix="MODEL_", extra="forbid")


class StorageConfig(BaseSettings):
    memory_store_type: Literal["redis", "in_memory", "database"] = "redis"
    event_store_type: Literal["redis", "in_memory"] = "in_memory"
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10

    model_config = SettingsConfigDict(env_prefix="STORAGE_", extra="forbid")


class SecurityConfig(BaseSettings):
    enable_rate_limiting: bool = True
    rate_limit_requests: int = 10
    rate_limit_window: int = 60
    audit_log_file: str = "./logs/audit.log"
    max_command_length: int = 10_000
    blocked_patterns: List[str] = Field(
        default_factory=lambda: [
            r".*\$\(.*rm.*\).*",
            r".*;\s*rm\s+.*",
            r".*&&\s*sudo.*",
        ]
    )

    @field_validator("rate_limit_requests", "max_command_length")
    def positive_int(cls, v):
        if v <= 0:
            raise ValueError("Must be positive")
        return v

    model_config = SettingsConfigDict(env_prefix="SECURITY_", extra="forbid")


class ObservabilityConfig(BaseSettings):
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: str = "./logs/agent.log"
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5
    enable_metrics: bool = True
    metrics_port: int = 8000

    @field_validator("log_level")
    def valid_log_level(cls, v):
        if v.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid log level")
        return v

    @field_validator("log_format")
    def valid_log_format(cls, v):
        if v not in {"json", "text"}:
            raise ValueError("log_format must be 'json' or 'text'")
        return v

    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", extra="forbid")


class CodingConfig(BaseSettings):
    workspace_root: str = "./user_workspaces"
    sandbox_timeout_seconds: int = 60
    sandbox_memory_mb: int = 512
    sandbox_disk_mb: int = 100

    @field_validator("workspace_root")
    def workspace_must_be_valid(cls, v):
        path = Path(v)
        if path.exists() and not path.is_dir():
            raise ValueError(f"Workspace root {v} exists but is not a directory")
        return v

    model_config = SettingsConfigDict(env_prefix="CODING_", extra="forbid")


class ProductionConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    coding: CodingConfig = Field(default_factory=CodingConfig)


def load_config() -> ProductionConfig:
    from dotenv import load_dotenv

    load_dotenv()
    return ProductionConfig()
