"""Shared rendering and selection of complete call/result interaction groups."""

from __future__ import annotations

import json
from typing import Any


def message_record(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return message
    return vars(message)


def message_calls(message: Any) -> list[dict[str, Any]]:
    record = message_record(message)
    metadata = record.get("metadata") or record.get("msg_metadata") or {}
    return record.get("tool_calls") or metadata.get("tool_calls") or []


def render_message(message: Any) -> str:
    record = message_record(message)
    content = record.get("content")
    text = (
        content
        if isinstance(content, str)
        else json.dumps(content, ensure_ascii=False, default=str)
        if content is not None
        else ""
    )
    calls = message_calls(record)
    if calls:
        text += "\nTool requests: " + json.dumps(calls, ensure_ascii=False, default=str)
    metadata = record.get("metadata") or record.get("msg_metadata") or {}
    call_id = record.get("tool_call_id") or metadata.get("tool_call_id")
    if record.get("role") == "tool" and call_id:
        text += f"\nTool call ID: {call_id}"
    return text


def interaction_groups(messages: list[Any]) -> list[list[Any]]:
    groups = []
    for message in messages:
        record = message_record(message)
        if record.get("role") == "tool" and groups and message_calls(groups[-1][0]):
            groups[-1].append(message)
        else:
            groups.append([message])
    return groups


def split_recent(
    messages: list[Any], count: int, *, expand=False
) -> tuple[list[Any], list[Any]]:
    """Split at a group boundary; expand only for active-context preserve_recent."""
    if count <= 0:
        return list(messages), []
    kept = []
    for group in reversed(interaction_groups(messages)):
        if len(kept) + len(group) > count and not expand:
            break
        kept = group + kept
        if len(kept) >= count:
            break
    return messages[: len(messages) - len(kept)], kept
