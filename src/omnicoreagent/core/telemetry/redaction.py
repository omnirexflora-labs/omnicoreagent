from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from functools import lru_cache
from typing import Any

from omnicoreagent.core.telemetry.payloads import TelemetryPayloadStore


REDACTION_MARKER = "[REDACTED]"

# Recording presets. ``default`` is privacy-first: model prompts and
# responses are not recorded. ``full`` records the complete trajectory,
# still redacted, truncated, and offloaded under the same policy.
CAPTURE_PRESETS: dict[str, dict[str, bool]] = {
    "default": {
        "record_inputs": True,
        "record_outputs": True,
        "record_model_prompts": False,
        "record_model_responses": False,
        "record_tool_results": True,
    },
    "full": {
        "record_inputs": True,
        "record_outputs": True,
        "record_model_prompts": True,
        "record_model_responses": True,
        "record_tool_results": True,
    },
}


class TelemetryPayloadError(RuntimeError):
    """Raised when configured oversized-payload storage is unavailable."""


@dataclass
class TelemetryConfig:
    # ``auto`` (like ``jsonl``) keeps every trace on local disk, in
    # telemetry/traces.jsonl under the workspace directory (./workspace unless
    # configured), even when the workspace itself is S3 or R2. ``memory``
    # keeps them in this process only. No external exporter is required.
    storage: str = "auto"
    # Where the jsonl store writes; None is telemetry/traces.jsonl in the workspace.
    storage_path: str | None = None
    # Finished traces older than this are pruned automatically; ``None`` keeps
    # every trace.
    retention_days: int | None = 7
    # The same for offloaded payloads, on their own window; a payload a kept
    # trace still refers to is never pruned.
    payload_retention_days: int | None = 7
    # Upper bound on finished traces kept by an in-memory store.
    memory_max_traces: int | None = 1000
    # A preset fills every ``record_*`` field left unset (``None``); a field
    # set explicitly always wins. After construction all are booleans.
    # ``full`` is the default (the maintainer's decision, 2026-09-22): a
    # trace is worth keeping only if it holds what the model was sent, and a
    # trainer or an evaluator cannot use one that does not. ``default`` is
    # the privacy-first preset for a deployment that must not record prompts.
    capture: str = "full"
    # What a trace keeps of each part of a run: inputs, outputs, what the model
    # was sent and what it answered, and what tools returned. None takes the
    # capture preset's value.
    record_inputs: bool | None = None
    record_outputs: bool | None = None
    record_model_prompts: bool | None = None
    record_model_responses: bool | None = None
    record_tool_results: bool | None = None
    # The tokens a model chose and their probabilities, when the provider
    # returns them (``logprobs``). Off: they are large, and only a trainer
    # reusing the run needs them.
    record_token_details: bool = False
    # A recorded payload larger than this is replaced by a truncated marker
    # with its size and checksum, unless offload_large_payloads keeps it whole.
    max_payload_bytes: int = 64_000
    # Keys whose values are replaced with [REDACTED] wherever they appear in a
    # recorded payload, at any depth.
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
    # Keep payloads over max_payload_bytes whole, in the workspace or object
    # storage, referenced from the trace, instead of truncating them.
    offload_large_payloads: bool = False
    offload_target: str = "workspace"
    # Where the archive of finished traces keeps its index and its bodies.
    # Unset is a SQLite file and a directory beside the log, which is what one
    # process needs. A database URL makes the index shared, and
    # ``archive_target="object_storage"`` puts the bodies in the deployment's
    # bucket, so several server processes can read one archive.
    archive_index_url: str | None = None
    archive_target: str = "local"
    # A directory the processes share, for a deployment that shares one host
    # rather than a bucket. Ignored when ``archive_target`` is object storage.
    archive_bodies_path: str | None = None
    # Raise when a record cannot be made safely (redaction or persistence
    # failed) instead of recording a marker and carrying on.
    strict: bool = False
    # Seconds a write to the trace store, or an export to an external
    # exporter, may take before it is given up and reported.
    persistence_timeout_seconds: float | None = 5.0
    export_timeout_seconds: float | None = 5.0

    def __post_init__(self) -> None:
        self.capture = str(self.capture).lower().strip()
        if self.capture not in CAPTURE_PRESETS:
            allowed = ", ".join(sorted(CAPTURE_PRESETS))
            raise ValueError(f"telemetry capture must be one of: {allowed}")
        for field_name, preset in CAPTURE_PRESETS[self.capture].items():
            value = getattr(self, field_name)
            if value is None:
                setattr(self, field_name, preset)
            elif not isinstance(value, bool):
                raise ValueError(f"telemetry {field_name} must be a bool or None")
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
        self.archive_target = str(self.archive_target).lower().strip()
        if self.archive_target not in {"local", "object_storage"}:
            raise ValueError(
                "telemetry archive_target must be local or object_storage"
            )
        if self.archive_index_url is not None and not str(self.archive_index_url).strip():
            raise ValueError("telemetry archive_index_url must not be empty")
        if self.archive_bodies_path is not None and not str(self.archive_bodies_path).strip():
            raise ValueError("telemetry archive_bodies_path must not be empty")
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


# What the model chose, under its provider's own field names (one of them is
# ``token``): its output, not a credential.
_VERBATIM_KEYS = frozenset({"token_details"})


def _redact(value: Any, redact_keys: set[str] | tuple[str, ...]) -> Any:
    if not isinstance(redact_keys, tuple):
        redact_keys = tuple(sorted(redact_keys))
    if isinstance(value, dict):
        return {
            key: REDACTION_MARKER
            if _should_redact_key(str(key), redact_keys)
            else item
            if key in _VERBATIM_KEYS
            else _redact(item, redact_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, redact_keys) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, redact_keys) for item in value]
    if isinstance(value, str):
        return _redact_encoded(value, redact_keys)
    return value


def _redact_encoded(value: str, redact_keys: tuple[str, ...]) -> str:
    """Redact inside a string that is itself JSON: a model's tool call
    carries its arguments that way, and key-based redaction never saw a
    secret in them. Text that is not JSON, or holds no redacted key, is
    returned as written."""
    stripped = value.strip()
    if len(stripped) < 2 or (stripped[0], stripped[-1]) not in (("{", "}"), ("[", "]")):
        return value
    lowered = stripped.lower()
    if not any(key in lowered for key in redact_keys):
        return value
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return value
    if not isinstance(decoded, (dict, list)):
        return value
    redacted = _redact(decoded, redact_keys)
    if redacted == decoded:
        return value
    return json.dumps(redacted, ensure_ascii=False)


# A key whose last word is one of these names a quantity, unit, or category,
# not a credential: ``max_tokens``, ``prompt_token_count``, ``token_type``,
# ``time_to_first_delta_ms``.
_NON_SECRET_LAST_WORDS = frozenset(
    {
        "budget",
        "bytes",
        "count",
        "counts",
        "details",
        "limit",
        "limits",
        "ms",
        "seconds",
        "tokens",
        "type",
        "usage",
    }
)


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def _split_key_words(key: str) -> tuple[str, ...]:
    snake = _CAMEL_BOUNDARY.sub("_", key)
    return tuple(word for word in _NOT_A_WORD.split(snake.lower()) if word)


@lru_cache(maxsize=4096)
def _key_words(key: str) -> tuple[str, ...]:
    """The words of a key, split once: keys repeat thousands of times a run."""
    return _split_key_words(key)


@lru_cache(maxsize=8192)
def _decide_key(key: str, patterns: tuple[str, ...]) -> bool:
    words = _key_words(key)
    if not words or words[-1] in _NON_SECRET_LAST_WORDS:
        return False
    joined = f"_{'_'.join(words)}_"
    compact = "".join(words)
    for pattern in patterns:
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


def _should_redact_key(key: str, redact_keys: set[str] | tuple[str, ...]) -> bool:
    """Whether a key names a secret, under these patterns.

    The decision for a key does not change while the patterns do not, and a
    request asks it thousands of times for a few dozen distinct keys, so it is
    made once per (key, patterns) and remembered.
    """
    patterns = redact_keys if isinstance(redact_keys, tuple) else tuple(sorted(redact_keys))
    return _decide_key(key, patterns)


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
