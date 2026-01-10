"""
Tiktoken-based token counting for accurate context management.

This module provides proper token counting using tiktoken instead of
simple word splitting, ensuring accurate token budget management.
"""

import tiktoken
from functools import lru_cache
from typing import Any


DEFAULT_ENCODING = "cl100k_base"

DEFAULT_SUMMARY_RATIO = 0.2


@lru_cache(maxsize=8)
def get_encoding(model: str = "gpt-4") -> tiktoken.Encoding:
    """
    Get tiktoken encoding for a model with caching.

    Args:
        model: The model name (e.g., "gpt-4", "gpt-3.5-turbo", "claude-3")

    Returns:
        tiktoken.Encoding: The appropriate encoding for the model
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(DEFAULT_ENCODING)


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
        content = msg.get("content", "")
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
