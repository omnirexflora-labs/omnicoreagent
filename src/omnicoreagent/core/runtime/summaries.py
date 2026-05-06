from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime, runtime_logger


def summary_instruction(max_tokens: int | None = None) -> str:
    instruction = runtime("FAST_CONVERSATION_SUMMARY_PROMPT")
    if max_tokens:
        instruction += f" Keep the summary roughly under {max_tokens} tokens."
    return instruction


def render_history(messages: list[dict[str, Any]]) -> str:
    return "".join(
        f"{message.get('role', 'unknown')}: {message.get('content', '')}\n"
        for message in messages
    )


def extract_summary_text(response: Any) -> str:
    if not response:
        return ""

    if hasattr(response, "choices") and response.choices:
        return response.choices[0].message.content.strip()
    if hasattr(response, "message"):
        return response.message.content.strip()
    if hasattr(response, "text"):
        return response.text.strip()
    if hasattr(response, "content"):
        return response.content.strip()
    if isinstance(response, dict) and "choices" in response:
        return response["choices"][0]["message"]["content"].strip()
    if isinstance(response, str):
        return response

    runtime_logger().error(
        f"No valid response content found in LLM response: {type(response)}"
    )
    return ""
