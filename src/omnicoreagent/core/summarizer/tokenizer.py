"""
Token counting helpers for context management.

Uses tiktoken when installed, with a lightweight word-count fallback for core
installations.
"""

import hashlib
import logging
import threading
from collections import OrderedDict
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


# The count of a text never changes, but the same texts were counted again at
# every step: the whole context for the context-size check, and again for the
# budget's estimate of the next call. On the support desk ramp (2026-10-07)
# that was 12% of the event loop's time. A count is kept by a digest of the
# text, never by the text, so the cache holds integers and the text can be
# freed. Short texts are encoded outright: hashing them costs about as much.
_COUNT_CACHE_ENTRIES = 8192
_COUNT_CACHE_MIN_CHARS = 128
_count_cache: "OrderedDict[tuple[str, bytes], int]" = OrderedDict()
_count_cache_lock = threading.Lock()


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
    if len(text) < _COUNT_CACHE_MIN_CHARS:
        return len(encoding.encode(text))
    key = (model, hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest())
    with _count_cache_lock:
        known = _count_cache.get(key)
        if known is not None:
            _count_cache.move_to_end(key)
            return known
    count = len(encoding.encode(text))
    with _count_cache_lock:
        _count_cache[key] = count
        while len(_count_cache) > _COUNT_CACHE_ENTRIES:
            _count_cache.popitem(last=False)
    return count


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
