"""Provider continuation data outside the model request: masking and summary.

Signatures and encrypted reasoning are opaque values a provider needs back
unchanged. They are never recorded in telemetry and never sent to the
summarizer; telemetry records only that they were present (counts and a
digest).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Keys whose values are opaque provider data.
OPAQUE_KEYS = frozenset(
    {"signature", "thought_signature", "thought_signatures", "encrypted_content"}
)
# Blocks that are opaque as a whole (their ``data`` is encrypted).
OPAQUE_BLOCK_TYPES = frozenset({"redacted_thinking", "reasoning.encrypted"})
# LiteLLM encodes a Gemini thought signature in the tool-call ID.
_SIGNED_ID = re.compile(r"__thought__([A-Za-z0-9+/=_\-]+)")


def opaque_marker(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    return f"[opaque sha256:{digest} len={len(text)}]"


def mask_opaque(value: Any) -> Any:
    """A copy with every opaque value replaced by a stable marker.

    The marker depends only on the value, so masked IDs still match each other
    and digests of masked data stay stable.
    """
    if isinstance(value, dict):
        # A block type is a string; a JSON-schema "type" may be a list or a
        # dict, and either would make this membership test raise.
        kind = value.get("type")
        if isinstance(kind, str) and kind in OPAQUE_BLOCK_TYPES:
            return {
                key: item if key in {"type", "index", "format", "id"} else opaque_marker(item)
                for key, item in value.items()
            }
        return {
            key: _mask_opaque_value(item) if key in OPAQUE_KEYS else mask_opaque(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_opaque(item) for item in value]
    if isinstance(value, str) and "__thought__" in value:
        return _SIGNED_ID.sub(lambda match: "__thought__" + opaque_marker(match.group(1)), value)
    return value


def _mask_opaque_value(value: Any) -> Any:
    if isinstance(value, list):
        return [opaque_marker(item) for item in value]
    return opaque_marker(value) if value is not None else None


def strip_signed_id(call_id: Any) -> Any:
    """A tool-call ID without an encoded thought signature (for summaries)."""
    if isinstance(call_id, str) and "__thought__" in call_id:
        return call_id.split("__thought__", 1)[0]
    return call_id


def continuation_summary(turn: Any) -> dict[str, Any] | None:
    """Counts and a digest of a turn's continuation data, or None if it has none."""
    fields = dict(getattr(turn, "provider_fields", None) or {})
    fields.pop("reasoning_content", None)
    calls = [
        (call.id, getattr(call, "provider_fields", None) or {})
        for call in getattr(turn, "tool_calls", ()) or ()
    ]
    signed_calls = [
        1
        for call_id, call_fields in calls
        if call_fields or (isinstance(call_id, str) and "__thought__" in call_id)
    ]
    if not fields and not signed_calls:
        return None
    blocks = fields.get("thinking_blocks") or []
    details = (fields.get("provider_specific_fields") or {}).get("reasoning_details") or []
    canonical = json.dumps(
        {"fields": fields, "calls": [[call_id, call_fields] for call_id, call_fields in calls]},
        sort_keys=True,
        default=str,
    )
    return {
        "thinking_blocks": sum(1 for block in blocks if block.get("type") == "thinking"),
        "redacted_thinking": sum(1 for block in blocks if block.get("type") == "redacted_thinking"),
        "reasoning_items": len(fields.get("reasoning_items") or []),
        "reasoning_details": len(details),
        "tool_call_signatures": len(signed_calls),
        "digest": hashlib.sha256(canonical.encode()).hexdigest()[:16],
    }
