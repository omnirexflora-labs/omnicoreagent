"""
Token counting helpers for context management.

Uses tiktoken when installed, with a lightweight word-count fallback for core
installations.
"""

import logging
from functools import lru_cache
from typing import Any
from omnicoreagent.core.interaction_history import render_message

logger = logging.getLogger(__name__)


DEFAULT_ENCODING = "cl100k_base"

DEFAULT_SUMMARY_RATIO = 0.2


@lru_cache(maxsize=8)
def get_encoding(model: str = "gpt-4") -> Any:
    """
    Get tiktoken encoding for a model with caching.

    Args:
        model: The model name (e.g., "gpt-4", "gpt-3.5-turbo", "claude-3")

    Returns:
        tiktoken.Encoding: The appropriate encoding for the model
    """
    try:
        import tiktoken
    except ModuleNotFoundError:
        return None
    try:
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding(DEFAULT_ENCODING)
    except Exception as exc:
        # tiktoken downloads an encoding on first use. Where the network is
        # closed — a task that allows only the model's host — counting falls
        # back to an estimate; a count is not a reason for the run to fail.
        # Cached, so this is said once, not on every count.
        logger.warning(
            "Token counting uses an estimate: the tiktoken encoding for %s could "
            "not be loaded (%s: %s). Set TIKTOKEN_CACHE_DIR to a directory "
            "holding it to count exactly without the network.",
            model,
            type(exc).__name__,
            exc,
        )
        return None


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Count tokens in text using tiktoken.

    Args:
        text: The text to count tokens for
        model: The model name for encoding selection

    Returns:
        int: Number of tokens in the text
    """
    if not text:
        return 0
    encoding = get_encoding(model)
    if encoding is None:
        return estimate_tokens_simple(text)
    return len(encoding.encode(text))


def count_message_tokens(messages: list[dict[str, Any]], model: str = "gpt-4") -> int:
    """
    Count total tokens across multiple messages.

    Args:
        messages: List of message dictionaries with 'content' field
        model: The model name for encoding selection

    Returns:
        int: Total token count across all messages
    """
    total = 0
    for msg in messages:
        content = render_message(msg)
        if content:
            total += count_tokens(str(content), model)
    return total


def estimate_tokens_simple(text: str) -> int:
    """
    Simple token estimation using word count (fallback if tiktoken unavailable).

    This is a rough estimate: ~1.3 tokens per word on average.

    Args:
        text: The text to estimate tokens for

    Returns:
        int: Estimated token count
    """
    if not text:
        return 0
    words = len(str(text).split())
    return int(words * 1.3)


def truncate_text_to_tokens(text: str, budget: int, model: str = "gpt-4") -> str:
    if budget <= 0:
        return ""
    encoding = get_encoding(model)
    if encoding is not None:
        return encoding.decode(encoding.encode(text)[:budget])
    words = text.split()
    while words and count_tokens(" ".join(words), model) > budget:
        words.pop()
    return " ".join(words)
