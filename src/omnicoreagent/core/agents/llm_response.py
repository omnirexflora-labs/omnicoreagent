from __future__ import annotations

from typing import Any

from omnicoreagent.core.token_usage import Usage


def extract_response_content(
    response: Any,
    *,
    strip: bool = True,
    default: str | None = None,
) -> str:
    """Extract text content from supported LLM response shapes."""
    try:
        turn = normalize_model_turn(response)
    except ValueError:
        if default is not None:
            return default
        raise ValueError(
            f"No valid response content found in LLM response: {type(response)}"
        ) from None
    if turn.tool_calls:
        raise ValueError("Expected a text-only response, received tool calls")
    return turn.text.strip() if strip else turn.text


def extract_response_usage(response: Any) -> Usage | None:
    """Extract token usage from supported LLM response objects."""
    raw_usage = getattr(response, "usage", None)
    if raw_usage is None and isinstance(response, dict):
        raw_usage = response.get("usage")
    if raw_usage is None:
        return None
    if isinstance(raw_usage, Usage):
        return raw_usage

    def get(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    def get_value(name: str) -> int:
        return int(get(raw_usage, name) or 0)

    details: dict[str, int] = {}
    cached = get(get(raw_usage, "prompt_tokens_details"), "cached_tokens")
    reasoning = get(get(raw_usage, "completion_tokens_details"), "reasoning_tokens")
    if cached:
        details["cached_input_tokens"] = int(cached)
    if reasoning:
        details["reasoning_tokens"] = int(reasoning)
    return Usage(
        requests=1,
        request_tokens=get_value("prompt_tokens"),
        response_tokens=get_value("completion_tokens"),
        total_tokens=get_value("total_tokens"),
        details=details,
    )


def normalize_model_turn(response: Any):
    """Retain the first completion's structured message without interpreting text."""
    from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest

    def get(value, key, default=None):
        return (
            value.get(key, default)
            if isinstance(value, dict)
            else getattr(value, key, default)
        )

    if isinstance(response, ModelTurn):
        return response
    if response is None:
        raise ValueError("Model returned no response")
    if isinstance(response, str):
        return ModelTurn(content=response)

    choices = get(response, "choices")
    if choices is not None:
        if not choices:
            raise ValueError("Model returned no choices")
        choice = choices[0]
        message = get(choice, "message")
        finish_reason = get(choice, "finish_reason")
    else:
        message = get(response, "message", response)
        finish_reason = get(response, "finish_reason")
    if message is None:
        raise ValueError("Model returned no message")

    content = get(message, "content", get(message, "text"))
    if isinstance(content, list):
        content = [
            block.model_dump() if hasattr(block, "model_dump") else block
            for block in content
        ]
        if not all(isinstance(block, dict) for block in content):
            raise ValueError("Model content blocks must be objects")
    elif content is not None and not isinstance(content, str):
        raise ValueError("Model content must be text or content blocks")

    calls = []
    for call in get(message, "tool_calls", []) or []:
        if get(call, "type", "function") != "function":
            raise ValueError("Unsupported model tool call type")
        function = get(call, "function")
        calls.append(
            ToolRequest(
                id=get(call, "id"),
                name=get(function, "name"),
                arguments=get(function, "arguments"),
            )
        )
    if len({call.id for call in calls}) != len(calls):
        raise ValueError("Model returned duplicate tool call IDs")
    refusal = get(message, "refusal")
    if content is None and not calls and refusal is None:
        raise ValueError("Model message has neither content nor tool calls")
    return ModelTurn(
        content=content,
        tool_calls=tuple(calls),
        finish_reason=finish_reason,
        usage=extract_response_usage(response),
        refusal=refusal,
        provider_fields={
            key: get(message, key)
            for key in ("reasoning_content",)
            if get(message, key) is not None
        },
        response_metadata={
            key: get(response, key)
            for key in ("id", "model")
            if get(response, key) is not None
        },
    )
