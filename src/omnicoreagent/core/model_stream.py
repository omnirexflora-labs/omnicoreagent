"""Incremental assembly of provider chat deltas; never executes tools."""

from __future__ import annotations

from typing import Any

from omnicoreagent.core.agents.llm_response import plain_copy, normalize_model_turn


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
        self.response_id = None
        self.response_model = None
        # Continuation data, assembled exactly as a non-streamed turn has it.
        self.thinking_blocks: list[dict[str, Any]] = []
        self._open_thinking: dict[str, Any] | None = None
        self.reasoning_items: list[Any] = []
        self.provider_fields: dict[str, Any] = {}
        self._reasoning_details: dict[tuple, dict[str, Any]] = {}

    def feed(self, chunk: Any) -> list[dict[str, Any]]:
        if field(chunk, "usage") is not None:
            self.usage = field(chunk, "usage")
        self.response_id = self.response_id or field(chunk, "id")
        self.response_model = self.response_model or field(chunk, "model")
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
            self._feed_continuation(delta)
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
                # Per-call continuation data (Gemini's thought signature).
                for target, source in (
                    (call, field(part, "provider_specific_fields")),
                    (call["function"], field(function, "provider_specific_fields")),
                ):
                    if source:
                        target.setdefault("provider_specific_fields", {}).update(plain_copy(source))
            finish = field(choice, "finish_reason")
            if finish is not None:
                if self.finish_reason is not None and finish != self.finish_reason:
                    raise ValueError("Conflicting stream finish reasons")
                self.finish_reason = finish
        return events

    def _feed_continuation(self, delta: Any) -> None:
        for block in field(delta, "thinking_blocks", None) or []:
            block = plain_copy(block)
            if block.get("type") == "thinking":
                # Text pieces append to the open block; the signature closes it.
                # (LiteLLM repeats the whole text with the signature; appending
                # that would double it, so the block's own pieces are kept.)
                if self._open_thinking is None:
                    self._open_thinking = {"type": "thinking", "thinking": ""}
                signature = block.get("signature")
                if signature:
                    if not self._open_thinking["thinking"]:
                        self._open_thinking["thinking"] = block.get("thinking") or ""
                    self._open_thinking["signature"] = signature
                    self._close_thinking()
                else:
                    self._open_thinking["thinking"] += block.get("thinking") or ""
            else:
                self._close_thinking()
                self.thinking_blocks.append(block)
        self.reasoning_items.extend(plain_copy(field(delta, "reasoning_items", None) or []))
        extra = plain_copy(field(delta, "provider_specific_fields", None) or {})
        details = field(delta, "reasoning_details", None) or extra.pop("reasoning_details", None)
        for position, piece in enumerate(plain_copy(details or [])):
            self._merge_reasoning_detail(position, piece)
        # LiteLLM repeats the thinking pieces here; they are kept above.
        extra.pop("thinking_blocks", None)
        self.provider_fields.update(extra)

    def _merge_reasoning_detail(self, position: int, piece: dict[str, Any]) -> None:
        key = (piece.get("index", position), piece.get("type"))
        merged = self._reasoning_details.get(key)
        if merged is None:
            self._reasoning_details[key] = dict(piece)
            return
        for name, value in piece.items():
            if name in {"text", "summary"} and isinstance(value, str):
                merged[name] = (merged.get(name) or "") + value
            elif value is not None:
                merged[name] = value

    def _close_thinking(self) -> None:
        if self._open_thinking is not None:
            self.thinking_blocks.append(self._open_thinking)
            self._open_thinking = None

    def finish(self):
        if self.finish_reason is None:
            raise ValueError("Provider stream ended without a terminal finish reason")
        if self.finish_reason == "tool_calls" and not self.calls:
            raise ValueError("Provider finished with tool_calls but supplied no calls")
        message = {
            "content": self.text or (None if self.calls else ""),
            "tool_calls": [self.calls[index] for index in sorted(self.calls)],
        }
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        self._close_thinking()
        if self.thinking_blocks:
            message["thinking_blocks"] = self.thinking_blocks
        if self.reasoning_items:
            message["reasoning_items"] = self.reasoning_items
        provider_fields = dict(self.provider_fields)
        if self._reasoning_details:
            provider_fields["reasoning_details"] = list(self._reasoning_details.values())
        if provider_fields:
            message["provider_specific_fields"] = provider_fields
        if self.refusal:
            message["refusal"] = self.refusal
        return normalize_model_turn(
            {
                "id": self.response_id,
                "model": self.response_model,
                "choices": [{"message": message, "finish_reason": self.finish_reason}],
                "usage": self.usage,
            }
        )
