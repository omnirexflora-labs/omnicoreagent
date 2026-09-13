from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime
from omnicoreagent.core.interaction_history import render_message


def summary_instruction(max_tokens: int | None = None) -> str:
    instruction = runtime("FAST_CONVERSATION_SUMMARY_PROMPT")
    if max_tokens:
        instruction += f" Keep the summary roughly under {max_tokens} tokens."
    return instruction


def render_history(messages: list[dict[str, Any]]) -> str:
    return "".join(
        f"{message.get('role', 'unknown')}: {render_message(message)}\n"
        for message in messages
    )


def extract_summary_text(response: Any) -> str:
    from omnicoreagent.core.agents.llm_response import extract_response_content

    return extract_response_content(response, default="")
