from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from typing import Any

from omnicoreagent.core.telemetry.payloads import TelemetryPayloadStore


REDACTION_MARKER = "[REDACTED]"


class TelemetryPayloadError(RuntimeError):
    """Raised when configured oversized-payload storage is unavailable."""


@dataclass
class TelemetryConfig:
    # ``auto`` keeps the lightweight in-memory fallback unless a local
    # workspace was explicitly configured at the runtime boundary. ``jsonl``
    # is the built-in durable local option; no external exporter is required.
    storage: str = "auto"
    storage_path: str | None = None
    # Finished traces older than this are pruned automatically; ``None`` keeps
    # every trace. Payloads have their own window, and a payload referenced by
    # a kept trace is never pruned.
    retention_days: int | None = 7
    payload_retention_days: int | None = 7
    # Upper bound on finished traces kept by an in-memory store.
    memory_max_traces: int | None = 1000
    record_inputs: bool = True
    record_outputs: bool = True
    record_model_prompts: bool = False
    record_model_responses: bool = False
    record_tool_results: bool = True
    max_payload_bytes: int = 64_000
    redact_keys: list[str] = field(
        default_factory=lambda: [
            "access_token",
            "api_key",
            "apikey",
            "authorization",
            "client_secret",
            "cookie",
            "password",
            "refresh_token",
            "secret",
            "set-cookie",
            "token",
        ]
    )
    offload_large_payloads: bool = False
    offload_target: str = "workspace"
    strict: bool = False
    persistence_timeout_seconds: float | None = 5.0
    export_timeout_seconds: float | None = 5.0

    def __post_init__(self) -> None:
        self.storage = str(self.storage).lower().strip()
        if self.storage not in {"auto", "memory", "jsonl"}:
            raise ValueError(
                "telemetry storage must be one of: auto, memory, jsonl"
            )
        if self.storage_path is not None and not str(self.storage_path).strip():
            raise ValueError("telemetry storage_path must not be empty")
        self.offload_target = str(self.offload_target).lower().strip()
        if self.offload_target not in {"workspace", "object_storage"}:
            raise ValueError(
                "telemetry offload_target must be workspace or object_storage"
            )
        if self.retention_days is not None and self.retention_days < 0:
            raise ValueError("telemetry retention_days must be non-negative or None")
        if self.payload_retention_days is not None and self.payload_retention_days < 0:
            raise ValueError(
                "telemetry payload_retention_days must be non-negative or None"
            )
        if self.memory_max_traces is not None and self.memory_max_traces < 1:
            raise ValueError("telemetry memory_max_traces must be positive or None")
        for field_name in ("persistence_timeout_seconds", "export_timeout_seconds"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"telemetry {field_name} must be non-negative, finite, or None"
                )

    @classmethod
    def from_value(cls, value: "TelemetryConfig | dict[str, Any] | None") -> "TelemetryConfig | None":
        """Normalize the public telemetry configuration boundary.

        ``None`` is preserved so an explicitly supplied recorder can remain the
        source of its own configuration.  Dictionaries are accepted at the
        facade boundary for parity with the other runtime configuration objects.
        """
        if value is None or isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError("telemetry_config must be a TelemetryConfig, dict, or None")

    def fingerprint(self) -> str:
        """Return a stable, non-secret identifier for this effective policy."""
        payload = asdict(self)
        # A path identifies a deployment location rather than the recording
        # policy and may contain a local username or project name.
        payload.pop("storage_path", None)
        payload["redact_keys"] = sorted(str(key).lower() for key in self.redact_keys)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def redact_payload(
    value: Any,
    config: TelemetryConfig,
    *,
    payload_store: TelemetryPayloadStore | None = None,
) -> Any:
    redacted = _redact(value, {key.lower() for key in config.redact_keys})
    return _truncate_or_reference(redacted, config, payload_store=payload_store)


def redact_sensitive_payload(value: Any, config: TelemetryConfig) -> Any:
    """Apply telemetry key redaction without truncating or offloading.

    This is used for privacy-safe context fingerprints. The same redacted
    representation is the input to normal payload capture; size limits are
    applied only when the payload is persisted.
    """

    return _redact(value, {key.lower() for key in config.redact_keys})


_CREDENTIAL_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_INLINE_ASSIGNMENT = re.compile(
    r"""(?P<key>[A-Za-z_][\w-]*)(?P<sep>["']?\s*[=:]\s*)(?P<quote>["']?)"""
    r"""(?P<value>[^\s"',;&}]+)"""
)


def redact_sensitive_text(value: str, config: TelemetryConfig) -> str:
    """Redact ``key=value`` credentials and auth schemes inside free text.

    Error messages and stacks are strings rather than mappings, so key-based
    payload redaction cannot see a secret embedded in them.
    """

    redact_keys = {key.lower() for key in config.redact_keys}
    text = _CREDENTIAL_SCHEME.sub(
        lambda match: f"{match.group(1)} {REDACTION_MARKER}", value
    )

    def replace(match: re.Match[str]) -> str:
        if not _should_redact_key(match.group("key"), redact_keys):
            return match.group(0)
        if match.group("value") == REDACTION_MARKER:
            return match.group(0)
        return (
            f"{match.group('key')}{match.group('sep')}"
            f"{match.group('quote')}{REDACTION_MARKER}"
        )

    return _INLINE_ASSIGNMENT.sub(replace, text)


def _redact(value: Any, redact_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTION_MARKER
            if _should_redact_key(str(key), redact_keys)
            else _redact(item, redact_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, redact_keys) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, redact_keys) for item in value]
    return value


# A key whose last word is one of these names a quantity or category, not a
# credential: ``max_tokens``, ``prompt_token_count``, ``token_type``.
_NON_SECRET_LAST_WORDS = frozenset(
    {"count", "counts", "details", "limit", "limits", "budget", "tokens", "type", "usage"}
)


def _key_words(key: str) -> list[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return [word for word in re.split(r"[^a-z0-9]+", snake.lower()) if word]


def _should_redact_key(key: str, redact_keys: set[str]) -> bool:
    words = _key_words(key)
    if not words or words[-1] in _NON_SECRET_LAST_WORDS:
        return False
    joined = f"_{'_'.join(words)}_"
    compact = "".join(words)
    for pattern in redact_keys:
        pattern_words = _key_words(pattern)
        if not pattern_words:
            continue
        # Match whole words (``client_secret``) or a run-together suffix
        # (``sessiontoken``), never a fragment such as ``tokenizer``.
        if f"_{'_'.join(pattern_words)}_" in joined or compact.endswith(
            "".join(pattern_words)
        ):
            return True
    return False


def _truncate_or_reference(
    value: Any,
    config: TelemetryConfig,
    *,
    payload_store: TelemetryPayloadStore | None = None,
) -> Any:
    max_payload_bytes = config.max_payload_bytes
    if max_payload_bytes <= 0:
        return {"truncated": True, "reason": "max_payload_bytes<=0"}
    encoded = json.dumps(value, sort_keys=True, default=str).encode(
        "utf-8",
        errors="replace",
    )
    if len(encoded) <= max_payload_bytes:
        return value
    checksum = hashlib.sha256(encoded).hexdigest()
    if config.offload_large_payloads:
        if payload_store is None:
            raise TelemetryPayloadError(
                "telemetry payload offload is enabled but no payload store is configured"
            )
        reference = payload_store.write(
            value,
            checksum=checksum,
            content_type="application/json",
        )
        return {
            "offloaded": True,
            "target": config.offload_target,
            "reference": reference,
            "original_bytes": len(encoded),
            "content_type": "application/json",
            "checksum": checksum,
        }
    return {
        "truncated": True,
        "original_bytes": len(encoded),
        "preview": encoded[:max_payload_bytes].decode("utf-8", errors="replace"),
    }
