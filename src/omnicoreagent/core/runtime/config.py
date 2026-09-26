from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from enum import Enum
from os import PathLike
from typing import Any
import difflib
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from omnicoreagent.core.privacy import PrivacyConfig
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
}


GUARDRAIL_MODES = frozenset({"off", "input_only", "full"})


# Providers that cannot restrict a sandbox's traffic to named hosts.
_PROVIDERS_WITHOUT_HOST_ALLOWLIST = frozenset({"docker", "e2b", "vercel", "local"})


def normalize_guardrail_mode(value: Any) -> str:
    """Normalize and validate the guardrail enforcement boundary."""
    if not isinstance(value, str):
        raise ValueError(
            "guardrail_mode must be one of: off, input_only, full"
        )
    mode = value.strip().lower()
    if mode not in GUARDRAIL_MODES:
        allowed = ", ".join(sorted(GUARDRAIL_MODES))
        raise ValueError(f"guardrail_mode must be one of: {allowed}; got '{value}'")
    return mode


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


@dataclass
class ModelConfig:
    provider: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = 5000
    max_context_length: int | None = 100000
    top_p: float | None = None
    reasoning_effort: str | None = None
    # Ask the provider for the tokens it chose and their probabilities, for
    # a trainer that will reuse the run (traces for training plan, R3).
    logprobs: bool | None = None
    top_logprobs: int | None = None
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
    cwd: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    # HTTP transports only; the transports default them to 60 s and 120 s.
    timeout: float | None = None
    sse_read_timeout: float | None = None
    auth: dict[str, Any] | None = None
    # Transport, handshake, and tool listing must finish within this.
    connect_timeout: float | None = 30.0
    # Per tool call; None leaves only the agent's tool timeout.
    call_timeout: float | None = None

    def __post_init__(self):
        self.transport_type = _transport_type(self.transport_type, self.name)
        if not self.name:
            self.name = _default_mcp_server_name(self)


def _default_mcp_server_name(tool: MCPToolConfig) -> str:
    """A stable name from what identifies the server, so an unnamed server
    keeps the same identity in governance and telemetry across runs."""
    source = Path(tool.command).name if tool.command else urlparse(tool.url or "").hostname
    base = re.sub(r"[^a-z0-9]+", "_", (source or "mcp").lower())
    identity = json.dumps(
        [tool.transport_type.value, tool.command, tool.args, tool.url], sort_keys=True
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:6]
    return f"{base.strip('_') or 'mcp'}_{digest}"


def _default_memory_config() -> dict[str, Any]:
    return {
        "mode": "sliding_window",
        "value": 10000,
        "summary": {"enabled": False, "retention_policy": "keep"},
    }


def _default_context_management() -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "token_budget",
        "value": 100000,
        "threshold_percent": 75,
        "strategy": "truncate",
        "preserve_recent": 4,
    }


def _default_tool_offload() -> dict[str, Any]:
    return {
        "enabled": True,
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
        # What a request, session, agent, or application may spend.
        "budgets": None,
        "project_root": None,
        "approval_resolver": None,
        # With no resolver: "suspend" pauses the run until a person decides,
        # "fail" refuses the call (the behaviour before durable runs).
        "approval_mode": "suspend",
        "sandbox_runtime": None,
        "sandbox_config": None,
        # What each run's sandbox is (network, image, working directory).
        "sandbox_manifest": None,
        # What the sandbox workspace bridge copies: {"include": [...],
        # "exclude": [...]} globs over workspace paths; None copies everything.
        "workspace_bridge": None,
        "allow_test_sandbox_runtime": False,
        "allow_static_high_risk_approvals": False,
    }


def _default_privacy_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "redact_telemetry": True,
        # The agent's conversation and files are its work; see PrivacyConfig.
        "redact_memory": False,
        "redact_workspace": False,
        "redact_stream": False,
        "redact_public": False,
        "redact_model_io": False,
        "categories": ["credit_card", "email", "phone", "ssn"],
    }


GOVERNANCE_CONFIG_KEYS = frozenset(_default_governance_config())


@dataclass
class AgentConfig:
    # The agent's name in its traces and records; OmniCoreAgent(name=...) sets it.
    agent_name: str = "OmniCoreAgent"
    # Recorded as the trace's agent_version; a content hash of the harness
    # (prompt, tools, model, and settings) is used when it is not set.
    agent_version: str | None = None
    # Most model calls one run may make; over it, the run ends with
    # termination_reason resource_limit. 0 is no limit. For limits in
    # dollars, use governance budgets.
    request_limit: int = 0
    # Most tokens (in and out) one run may use; over it, the run ends with
    # termination_reason resource_limit. 0 is no limit.
    total_tokens_limit: int = 0
    # Most model turns one run may take; then it ends with termination_reason
    # max_steps. 1 to 1000.
    max_steps: int = 50
    # Seconds one tool call may take before it fails with a timeout. 2 to 1000.
    tool_call_timeout: int = 180
    # Seconds a delegation (spawn_subagents) may take. None: a worker is
    # bounded by its own step cap and the run's deadline, not by the timeout
    # for one tool call.
    subagent_timeout: int | None = None
    # Accepted for compatibility with 0.3.x and has no effect: MCP is on when
    # mcp_tools are given.
    mcp_enabled: bool = False
    # For large tool sets: a tools_retriever tool the model uses to find tools
    # by describing what it needs; the matching tools are offered on the next turn.
    enable_advanced_tool_use: bool = False
    # Let the agent spawn focused workers (spawn_subagents), each under the same
    # policy and budgets with its own linked trace. Turns workspace files on.
    enable_subagents: bool = False
    # Offer the skills found in skills_dir: their instructions, files and scripts.
    enable_agent_skills: bool = False
    # Where skills are found; None is ./.agents/skills in the working directory.
    # A harness names its own, so the task's directory stays the task's.
    skills_dir: str | None = None
    # How many times a final answer is reviewed before it is accepted: the
    # model is asked for each requirement and the check that showed it, and
    # the run goes on in the same trace. Off by default.
    completion_review: int = 0
    # Environment variables host skill scripts receive beyond the minimal set
    # (PATH, HOME, locale, TMPDIR, TERM); secrets are not passed by default.
    skill_script_env: list[str] = field(default_factory=list)
    # A live run refreshes its heartbeat within this many seconds; a run whose
    # heartbeat is older can be recovered by another process.
    run_lease_seconds: int = 60
    # Finished run records (completed, failed, blocked, cancelled, timeout)
    # are removed from the memory store this many days after the run
    # started; None keeps every record forever. A run still waiting for a
    # person or a resume is never removed.
    run_retention_days: int | None = 30
    # Code mode: a run_code tool that runs Python in Monty (omnicoreagent[codemode]).
    code_mode: dict[str, Any] = field(default_factory=dict)
    # A project's own instructions for the agent (AGENTS.md), by path.
    agents_md: dict[str, Any] = field(default_factory=dict)
    # How much of a session's history a run is given: a sliding window of
    # messages or tokens, and optional summaries of what falls out of it.
    memory_config: dict[str, Any] = field(default_factory=_default_memory_config)
    # File tools over the agent's workspace (ls, read_file, write_file, edit_file,
    # glob, grep, ...). The workspace itself is set by workspace_config.
    enable_workspace_files: bool = True
    # The prompt-injection guardrail: guardrail_mode is full (input and tool
    # results), input_only, or off; guardrail_config tunes its detection.
    guardrail_config: dict[str, Any] = field(default_factory=dict)
    guardrail_mode: str = "full"
    # Personal data (email, phone, SSN, card numbers) redacted per boundary.
    # By default only the record (telemetry and its exports) is redacted; the
    # model, memory, files, stream and answer see the real data.
    privacy_config: dict[str, Any] = field(default_factory=_default_privacy_config)
    # Keeps a run's context under a token budget before each model call:
    # past threshold_percent of value, older messages are truncated or
    # summarized, keeping the preserve_recent latest.
    context_management: dict[str, Any] = field(
        default_factory=_default_context_management
    )
    # A tool result over the thresholds is saved to the workspace, and the
    # model gets a preview and an artifact it can read in full.
    tool_offload: dict[str, Any] = field(default_factory=_default_tool_offload)
    # The policy (a profile, a policy, or a policy file), budgets, the sandbox
    # provider and its manifest, and how unanswered approvals are handled. Off
    # by default; see the security model.
    governance_config: dict[str, Any] = field(default_factory=_default_governance_config)
    # Where the agent's workspace lives: workspace_dir on local disk (default
    # ./workspace), or S3 / R2 storage.
    workspace_config: WorkspaceConfig | dict[str, Any] | None = None

    def __post_init__(self):
        self.guardrail_mode = normalize_guardrail_mode(self.guardrail_mode)
        if self.agent_version is not None and (
            not isinstance(self.agent_version, str) or not self.agent_version.strip()
        ):
            raise ValueError("agent_version must be a non-empty string or None")
        if self.agents_md:
            from omnicoreagent.core.project_instructions import ProjectInstructionsConfig

            ProjectInstructionsConfig.from_value(self.agents_md)  # validates
        if self.code_mode:
            from omnicoreagent.core.tools.code_mode import CodeModeConfig

            CodeModeConfig.from_value(self.code_mode)  # validates
        if (
            isinstance(self.run_lease_seconds, bool)
            or not isinstance(self.run_lease_seconds, int)
            or self.run_lease_seconds < 1
        ):
            raise ValueError("run_lease_seconds must be a positive integer")
        if not isinstance(self.skill_script_env, (list, tuple)) or not all(
            isinstance(name, str) and name for name in self.skill_script_env
        ):
            raise ValueError("skill_script_env must be a list of environment variable names")
        self.skill_script_env = list(self.skill_script_env)
        if self.guardrail_config is None:
            # 0.3's own default; a config copied from it still means "none".
            self.guardrail_config = {}
        if not isinstance(self.guardrail_config, dict):
            raise ValueError("guardrail_config must be a dict")
        self.request_limit = 0 if self.request_limit is None else self.request_limit
        self.total_tokens_limit = (
            0 if self.total_tokens_limit is None else self.total_tokens_limit
        )
        self.guardrail_config = self.guardrail_config or {}
        if self.guardrail_mode != "off":
            # Keep the package import path lightweight; validation still
            # happens before any guarded runtime component is built.
            from omnicoreagent.core.guardrails.models import DetectionConfig

            # Validate the nested security policy at the public configuration
            # boundary, before model/tool construction can begin.
            DetectionConfig(**self.guardrail_config)
        if not isinstance(self.privacy_config, dict):
            raise ValueError("privacy_config must be a dict")
        self.privacy_config = _merge_defaults(
            _default_privacy_config(), self.privacy_config
        )
        PrivacyConfig(**self.privacy_config)
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
        if self.run_retention_days is not None and (
            isinstance(self.run_retention_days, bool)
            or not isinstance(self.run_retention_days, int)
            or self.run_retention_days < 1
        ):
            raise ValueError("run_retention_days must be a whole number of days (1 or more), or None to keep every run")
        _validate_range("completion_review", self.completion_review, minimum=0, maximum=3)
        _validate_range(
            "tool_call_timeout", self.tool_call_timeout, minimum=2, maximum=1000
        )
        if self.subagent_timeout is not None:
            _validate_range(
                "subagent_timeout", self.subagent_timeout, minimum=2, maximum=86400
            )
        _validate_context_management(self.context_management)
        _validate_tool_offload(self.tool_offload)
        _validate_governance_config(self.governance_config)

        if self.enable_subagents:
            # Dynamic workers depend on a durable file surface for their output,
            # in-run context management to prevent the lead and workers from
            # exhausting their context, and tool offloading to keep large
            # observations out of subsequent model requests.
            # Preserve all caller-supplied context settings, but do not allow
            # the required capability to be disabled by an incomplete config.
            self.enable_workspace_files = True
            self.context_management["enabled"] = True
            self.tool_offload["enabled"] = True

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


_MCP_COMMON_FIELDS = frozenset({"name", "transport_type", "connect_timeout", "call_timeout"})
_MCP_STDIO_FIELDS = frozenset({"command", "args", "cwd", "env"})
_MCP_HTTP_FIELDS = frozenset({"url", "headers", "timeout", "sse_read_timeout", "auth"})
_MCP_AUTH_FIELDS = frozenset({"method", "callback_port", "callback_timeout"})


def _transport_type(value: Any, name: str | None) -> TransportType:
    text = getattr(value, "value", value)
    text = "streamable_http" if text == "streamable-http" else text
    try:
        return TransportType(text)
    except ValueError:
        supported = ", ".join(t.value for t in TransportType)
        raise ValueError(
            f"MCP server {name!r}: Unsupported MCP transport_type {text!r}. "
            f"Supported: {supported}"
        ) from None


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _string_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def _validate_mcp_server(data: dict[str, Any]) -> None:
    """Reject settings that are wrong or that do nothing for the transport."""
    name = data.get("name")

    def fail(message: str) -> None:
        raise ValueError(f"MCP server {name!r}: {message}")

    transport = TransportType(data["transport_type"])
    other = _MCP_HTTP_FIELDS if transport == TransportType.STDIO else _MCP_STDIO_FIELDS
    other_names = "stdio" if transport != TransportType.STDIO else "sse, streamable_http"
    for field_name in sorted(other & set(data)):
        fail(f"'{field_name}' does not apply to {transport.value} (it applies to {other_names})")

    if transport == TransportType.STDIO:
        if not data.get("command"):
            fail("command is required for stdio transport")
    else:
        url = data.get("url")
        if not url:
            fail(f"url is required for {transport.value} transport")
        if not str(url).startswith(("http://", "https://")):
            fail("url must start with http:// or https://")

    if "args" in data and not (
        isinstance(data["args"], list) and all(isinstance(a, str) for a in data["args"])
    ):
        fail("args must be a list of strings")
    for field_name in ("env", "headers"):
        if field_name in data and not _string_map(data[field_name]):
            fail(f"{field_name} must map strings to strings")
    for field_name in ("timeout", "sse_read_timeout", "connect_timeout", "call_timeout"):
        if field_name in data and not _positive(data[field_name]):
            fail(f"{field_name} must be a positive number of seconds")

    auth = data.get("auth")
    if auth is not None:
        if not isinstance(auth, dict):
            fail("auth must be a mapping such as {'method': 'oauth'}")
        for key in sorted(set(auth) - _MCP_AUTH_FIELDS):
            fail(f"Unknown auth setting {key!r}. Allowed: {', '.join(sorted(_MCP_AUTH_FIELDS))}")
        if auth.get("method") != "oauth":
            fail("auth method must be 'oauth' (use headers for a static token)")
        port = auth.get("callback_port")
        if port is not None and not (
            isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
        ):
            fail("auth callback_port must be an integer from 1 to 65535")
        if "callback_timeout" in auth and not _positive(auth["callback_timeout"]):
            fail("auth callback_timeout must be a positive number of seconds")


def normalize_mcp_tool_config(config: dict[str, Any] | MCPToolConfig) -> dict[str, Any]:
    """Validate one MCP server's settings and return them as a plain mapping."""
    if isinstance(config, MCPToolConfig):
        tool = config
    else:
        known = _MCP_COMMON_FIELDS | _MCP_STDIO_FIELDS | _MCP_HTTP_FIELDS
        for key in sorted(set(config) - known):
            raise ValueError(
                f"MCP server {config.get('name')!r}: Unknown MCP server setting {key!r}. "
                f"Allowed: {', '.join(sorted(known))}"
            )
        tool = MCPToolConfig(**config)
    data = asdict(tool)
    data["transport_type"] = tool.transport_type.value
    data = {key: value for key, value in data.items() if value is not None}
    _validate_mcp_server(data)
    return data


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
        _check_agent_config_keys(config)
        data = AgentConfig(**{**config, "agent_name": name}).model_dump()
    elif config is None:
        data = AgentConfig(agent_name=name).model_dump()
    else:
        raise ValueError("agent_config must be a dict or AgentConfig")
    return data


# Keys 0.3 accepted that 0.4 does not, and what took their place.
_REMOVED_AGENT_CONFIG_KEYS = {
    "memory_tool_backend": (
        "the memory_* tools are gone: the agent's files live in its workspace, "
        "on by default (enable_workspace_files), on local disk or S3 / R2 "
        "through workspace_config"
    ),
}


def _check_agent_config_keys(config: dict[str, Any]) -> None:
    known = {item.name for item in fields(AgentConfig)}
    for key in config:
        if key in known:
            continue
        if key in _REMOVED_AGENT_CONFIG_KEYS:
            raise ValueError(
                f"agent_config[{key!r}] was removed in 0.4: "
                f"{_REMOVED_AGENT_CONFIG_KEYS[key]}. "
                "See https://docs-omnicoreagent.omnirexfloralabs.com/docs/upgrading"
            )
        close = difflib.get_close_matches(key, known, n=1)
        hint = f" Did you mean {close[0]!r}?" if close else ""
        raise ValueError(
            f"Unknown agent_config setting {key!r}.{hint} "
            f"Settings: {', '.join(sorted(known))}"
        )


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
    if value.get("approval_mode", "suspend") not in {"suspend", "fail"}:
        raise ValueError("governance_config.approval_mode must be 'suspend' or 'fail'")
    if not isinstance(value.get("allow_test_sandbox_runtime", False), bool):
        raise ValueError("governance_config.allow_test_sandbox_runtime must be a boolean")
    budgets = value.get("budgets")
    if budgets is not None:
        if not isinstance(budgets, dict):
            raise ValueError("governance_config.budgets must be a dict")
        from omnicoreagent.governance import PolicyBudgets

        PolicyBudgets(**budgets)  # read now, so a bad budget fails at startup
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
                frozenset({"provider", "options"}),
            )
            provider = sandbox_config.get("provider", "none")
        else:
            try:
                from omnicoreagent.sandbox import SandboxRuntimeConfig

                valid_config = isinstance(sandbox_config, SandboxRuntimeConfig)
                provider = sandbox_config.provider_name if valid_config else None
            except Exception:
                valid_config = False
                provider = None
            if not valid_config:
                raise ValueError("governance_config.sandbox_config must be a dict or string")
        from omnicoreagent.sandbox import registered_sandbox_providers

        if provider not in registered_sandbox_providers():
            raise ValueError(
                "governance_config.sandbox_config.provider must be a registered "
                f"sandbox provider: {', '.join(registered_sandbox_providers())}"
            )
    if value.get("sandbox_manifest") is not None:
        from omnicoreagent.sandbox.factory import sandbox_manifest_from_config

        manifest = sandbox_manifest_from_config(value["sandbox_manifest"])
        named = sandbox_config if isinstance(sandbox_config, str) else (
            sandbox_config.get("provider") if isinstance(sandbox_config, dict) else None
        )
        if (
            manifest is not None
            and manifest.network_policy.allowed_hosts
            and named in _PROVIDERS_WITHOUT_HOST_ALLOWLIST
        ):
            # Refused now, not after a person has been asked to turn the
            # network on for a rule the provider cannot keep.
            raise ValueError(
                f"The {named} sandbox cannot enforce a network host allowlist "
                "(allowed_hosts): use network_policy default 'deny' or 'allow', "
                "or a provider that enforces one (daytona, modal, http)"
            )
    bridge = value.get("workspace_bridge")
    if bridge is not None:
        if not isinstance(bridge, dict) or set(bridge) - {"include", "exclude"}:
            raise ValueError(
                "governance_config.workspace_bridge must be a dict with 'include' "
                "and/or 'exclude' lists of glob patterns"
            )
        for key, patterns in bridge.items():
            if patterns is not None and (
                not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns)
            ):
                raise ValueError(f"governance_config.workspace_bridge.{key} must be a list of glob strings")
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
