from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


REDACTION_MARKER = "[REDACTED]"


@dataclass
class TelemetryConfig:
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


def redact_payload(value: Any, config: TelemetryConfig) -> Any:
    redacted = _redact(value, {key.lower() for key in config.redact_keys})
    return _truncate_or_reference(redacted, config)


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


def _should_redact_key(key: str, redact_keys: set[str]) -> bool:
    normalized = key.replace("-", "_").lower()
    return any(pattern in normalized for pattern in redact_keys)


def _truncate_or_reference(value: Any, config: TelemetryConfig) -> Any:
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
        return {
            "offloaded": True,
            "target": config.offload_target,
            "reference": f"telemetry://payload/{checksum}",
            "original_bytes": len(encoded),
            "content_type": "application/json",
            "checksum": checksum,
        }
    return {
        "truncated": True,
        "original_bytes": len(encoded),
        "preview": encoded[:max_payload_bytes].decode("utf-8", errors="replace"),
    }
