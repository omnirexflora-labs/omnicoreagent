"""Read MCP SDK tools and call results in one place.

The mcp 2.x SDK exposes snake_case attributes (``input_schema``,
``structured_content``, ``is_error``) and serializes to the camelCase wire
names only with ``by_alias=True``. Everything that reads an MCP object goes
through here, so a future SDK change has one place to land.
"""

from __future__ import annotations

import json
from typing import Any


def mcp_tool_definition(tool: Any) -> dict[str, Any]:
    """Name, description, and input schema of an MCP tool object or dict."""
    if isinstance(tool, dict):
        schema = tool.get("inputSchema", tool.get("input_schema"))
        return {
            "name": tool.get("name", ""),
            "description": tool.get("description") or "",
            "inputSchema": schema or {},
        }
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": getattr(tool, "input_schema", None) or {},
    }


def is_mcp_call_result(result: Any) -> bool:
    return not isinstance(result, dict) and hasattr(result, "content")


def mcp_content_blocks(result: Any) -> list[dict[str, Any]]:
    """Content blocks as their wire form (``mimeType``, not ``mime_type``)."""
    blocks = []
    for block in getattr(result, "content", None) or []:
        if hasattr(block, "model_dump"):
            blocks.append(block.model_dump(by_alias=True, mode="json", exclude_none=True))
        elif isinstance(block, dict):
            blocks.append(block)
        else:
            blocks.append({"type": "text", "text": str(getattr(block, "text", block))})
    return blocks


def normalize_mcp_call_result(result: Any) -> tuple[str, Any, str | None]:
    """Return ``(status, data, message)`` for an MCP ``CallToolResult``.

    Structured content, when present, is the data. A text block that only
    repeats it (SDK servers add one) is dropped; any other block is kept
    beside it under ``content``. A scalar the server wrapped as
    ``{"result": value}`` is returned as the value.
    """
    blocks = mcp_content_blocks(result)
    structured = getattr(result, "structured_content", None)
    status = "error" if getattr(result, "is_error", False) else "success"

    if structured is not None:
        extra = [block for block in blocks if not _repeats(block, structured)]
        if extra:
            data: Any = {"structuredContent": structured, "content": extra}
        elif _is_wrapped_scalar(structured, blocks):
            # SDK servers wrap a scalar return as {"result": value}.
            data = structured["result"]
        else:
            data = structured
    elif len(blocks) == 1 and blocks[0].get("type") == "text":
        data = blocks[0].get("text", "")
    else:
        data = {"content": blocks}

    message = None
    if status == "error":
        text = "\n".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        message = text or (
            json.dumps(structured, ensure_ascii=False, default=str)
            if structured is not None
            else "MCP tool failed"
        )
    return status, data, message


def mcp_result_text(result: Any) -> str:
    """All text the model could receive from a result, for guardrail checks."""
    parts = [
        block.get("text", "")
        for block in mcp_content_blocks(result)
        if block.get("type") == "text"
    ]
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return " ".join(part for part in parts if part)


def _is_wrapped_scalar(structured: Any, blocks: list[dict[str, Any]]) -> bool:
    return (
        bool(blocks)
        and isinstance(structured, dict)
        and set(structured) == {"result"}
        and not isinstance(structured["result"], (dict, list))
    )


def _repeats(block: dict[str, Any], structured: Any) -> bool:
    if block.get("type") != "text":
        return False
    text = block.get("text", "")
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = text
    return parsed == structured or structured in ({"result": parsed}, {"result": text})
