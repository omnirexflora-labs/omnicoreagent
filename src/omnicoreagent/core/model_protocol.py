"""Provider-independent complete turns and correlated tool requests.

Text (including XML) is content. Only structured tool requests can cause effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from omnicoreagent.core.token_usage import Usage


@dataclass(frozen=True)
class ToolRequest:
    id: str
    name: str
    arguments: str

    def __post_init__(self):
        for name in ("id", "name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Tool request requires a nonempty {name}")
        if not isinstance(self.arguments, str):
            raise ValueError("Tool request arguments must be a JSON string")

    def decode_arguments(self) -> dict[str, Any]:
        def reject_constant(value):
            raise ValueError(f"Non-JSON numeric constant: {value}")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate argument key: {key}")
                result[key] = value
            return result

        value = json.loads(
            self.arguments,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
        if not isinstance(value, dict):
            raise ValueError("Tool arguments must decode to a JSON object")
        return value

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class ModelTurn:
    content: str | list[dict[str, Any]] | None = None
    tool_calls: tuple[ToolRequest, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None
    refusal: str | None = None
    provider_fields: dict[str, Any] = field(default_factory=dict)
    # Provider response identity (``id``, served ``model``). Evidence only;
    # never sent back to the model.
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(
            block["text"]
            for block in (self.content or [])
            if block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str)
        )

    def assistant_message(self) -> dict[str, Any]:
        message = {"role": "assistant", "content": self.content, **self.provider_fields}
        if self.tool_calls:
            message["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        if self.refusal is not None:
            message["refusal"] = self.refusal
        return message
