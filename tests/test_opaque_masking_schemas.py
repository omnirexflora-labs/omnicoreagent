"""A JSON-schema "type" is not a block type.

Found deploying the repository steward: recording the run configuration
digests the tool catalog, and the GitHub MCP server's schemas carry
``"type": ["string", "null"]`` and nested ``type`` objects, as JSON Schema
allows. The opaque-block check did ``value.get("type") in OPAQUE_BLOCK_TYPES``
and a list or dict is unhashable, so the first run of any agent with such a
tool died with ``TypeError: unhashable type`` before its first model call.
The privacy filter had the same check. A block type is a string; anything
else is not one.
"""

from __future__ import annotations

from omnicoreagent.core.continuation import mask_opaque
from omnicoreagent.core.privacy import PrivacyFilter

SCHEMA = {
    "name": "issue_write",
    "inputSchema": {
        "type": "object",
        "properties": {
            "labels": {"type": ["array", "null"], "items": {"type": "string"}},
            "body": {"type": {"const": "string"}},
        },
    },
}


def test_masking_leaves_json_schema_types_alone():
    assert mask_opaque(SCHEMA) == SCHEMA


def test_redacting_leaves_json_schema_types_alone():
    assert PrivacyFilter().redact(SCHEMA, boundary="telemetry") == SCHEMA


def test_an_opaque_block_is_still_masked():
    block = {"type": "redacted_thinking", "data": "encrypted-bytes", "index": 0}

    masked = mask_opaque(block)

    assert masked["type"] == "redacted_thinking" and masked["index"] == 0
    assert masked["data"] != "encrypted-bytes" and masked["data"].startswith("[opaque")
    assert PrivacyFilter().redact(block, boundary="telemetry") == block  # left whole
