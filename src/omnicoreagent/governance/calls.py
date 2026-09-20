"""The tool call on whose behalf authority is being requested.

A tool's body can ask for authority of its own — opening a sandbox session
asks for its network and image, each command in it asks ``process.exec`` —
and when the policy answers "ask", the approval must be recorded against the
tool call that needs it, or the run cannot pause on it and continue it later.
The governed tool runner marks the call here; the sandbox layer reads it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class ToolCallRef:
    tool_call_id: str
    tool_name: str
    tool_provider: str | None = None


_CURRENT: ContextVar[ToolCallRef | None] = ContextVar("omnicoreagent_tool_call", default=None)


def current_tool_call() -> ToolCallRef | None:
    return _CURRENT.get()


@contextmanager
def on_behalf_of(tool_call_id: str, tool_name: str, tool_provider: str | None = None) -> Iterator[None]:
    token = _CURRENT.set(ToolCallRef(tool_call_id, tool_name, tool_provider))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def tool_call_metadata() -> dict[str, Any]:
    """Authority-request metadata naming the current tool call, if any."""
    call = _CURRENT.get()
    if call is None:
        return {}
    metadata = {"tool_call_id": call.tool_call_id, "tool_name": call.tool_name}
    if call.tool_provider:
        metadata["tool_provider"] = call.tool_provider
    return metadata
