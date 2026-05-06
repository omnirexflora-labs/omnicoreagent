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
    content = _raw_response_content(response)
    if content is None:
        if default is not None:
            return default
        raise ValueError(f"No valid response content found in LLM response: {type(response)}")

    if not isinstance(content, str):
        content = str(content)
    return content.strip() if strip else content


def extract_response_usage(response: Any) -> Usage | None:
    """Extract token usage from supported LLM response objects."""
    raw_usage = getattr(response, "usage", None)
    if raw_usage is None and isinstance(response, dict):
        raw_usage = response.get("usage")
    if raw_usage is None:
        return None

    def get_value(name: str, default: int = 0) -> int:
        if isinstance(raw_usage, dict):
            value = raw_usage.get(name, default)
        else:
            value = getattr(raw_usage, name, default)
        return int(value or 0)

    return Usage(
        requests=1,
        request_tokens=get_value("prompt_tokens"),
        response_tokens=get_value("completion_tokens"),
        total_tokens=get_value("total_tokens"),
    )


def _raw_response_content(response: Any) -> Any:
    if response is None:
        return None

    choices = getattr(response, "choices", None)
    if choices:
        return choices[0].message.content

    if hasattr(response, "message"):
        return response.message.content
    if hasattr(response, "text"):
        return response.text
    if hasattr(response, "content"):
        return response.content

    if isinstance(response, dict):
        choices = response.get("choices")
        if choices:
            return choices[0]["message"]["content"]
        for key in ("message", "text", "content"):
            if key in response:
                value = response[key]
                if isinstance(value, dict) and "content" in value:
                    return value["content"]
                return value

    if isinstance(response, str):
        return response

    return None
