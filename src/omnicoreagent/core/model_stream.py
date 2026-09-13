"""Incremental assembly of provider chat deltas; never executes tools."""

from __future__ import annotations

from typing import Any

from omnicoreagent.core.agents.llm_response import normalize_model_turn


def field(value, name, default=None):
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


class ModelStreamAssembler:
    def __init__(self):
        self.text = ""
        self.reasoning = ""
        self.refusal = ""
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish_reason = None
        self.usage = None

    def feed(self, chunk: Any) -> list[dict[str, Any]]:
        if field(chunk, "usage") is not None:
            self.usage = field(chunk, "usage")
        events = []
        for choice in field(chunk, "choices", []) or []:
            if field(choice, "index", 0) != 0:
                continue
            delta = field(choice, "delta", {}) or {}
            text = field(delta, "content")
            calls = field(delta, "tool_calls", []) or []
            if self.finish_reason is not None and (text or calls):
                raise ValueError("Provider sent content after the completed turn")
            if text is not None:
                if not isinstance(text, str):
                    raise ValueError("Streaming content delta must be text")
                self.text += text
                if text:
                    events.append({"type": "text_delta", "text": text})
            self.reasoning += field(delta, "reasoning_content", "") or ""
            self.refusal += field(delta, "refusal", "") or ""
            for part in calls:
                index = field(part, "index")
                if not isinstance(index, int) or index < 0:
                    raise ValueError("Tool delta requires a nonnegative index")
                call = self.calls.setdefault(
                    index,
                    {
                        "id": None,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                id = field(part, "id")
                if id:
                    if call["id"] not in (None, id):
                        raise ValueError("Tool call ID changed within a stream")
                    call["id"] = id
                type = field(part, "type")
                if type is not None and type != "function":
                    raise ValueError("Unsupported streamed tool type")
                function = field(part, "function", {}) or {}
                name = field(function, "name")
                if name and name != call["function"]["name"]:
                    call["function"]["name"] += name
                arguments = field(function, "arguments", "") or ""
                if not isinstance(arguments, str):
                    raise ValueError("Tool argument delta must be text")
                call["function"]["arguments"] += arguments
            finish = field(choice, "finish_reason")
            if finish is not None:
                if self.finish_reason is not None and finish != self.finish_reason:
                    raise ValueError("Conflicting stream finish reasons")
                self.finish_reason = finish
        return events

    def finish(self):
        if self.finish_reason is None:
            raise ValueError("Provider stream ended without a terminal finish reason")
        message = {
            "content": self.text or (None if self.calls else ""),
            "tool_calls": [self.calls[index] for index in sorted(self.calls)],
        }
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.refusal:
            message["refusal"] = self.refusal
        return normalize_model_turn(
            {
                "choices": [{"message": message, "finish_reason": self.finish_reason}],
                "usage": self.usage,
            }
        )
