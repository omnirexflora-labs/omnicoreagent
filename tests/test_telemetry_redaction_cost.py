"""Audit A4: deciding whether a key is secret is cheap, and the same as before.

Profiled at steady state, ``_should_redact_key`` was the largest single cost
of a request: ~4,900 decisions per request, each re-splitting every pattern
in the redact list with two regexes compiled through ``re``'s cache. The
decision for a key never changes while the patterns do not, so it is made
once per key and remembered; the words of a key are split once. The results
are held to what the previous implementation returned, case by case.
"""

from __future__ import annotations

import re

from omnicoreagent.core.telemetry import redaction
from omnicoreagent.core.telemetry.redaction import (
    REDACTION_MARKER,
    TelemetryConfig,
    _should_redact_key,
    redact_payload,
)

# What the decision used to be, kept as the thing to match.
_NON_SECRET = {
    "budget", "bytes", "count", "counts", "details", "limit", "limits", "ms",
    "seconds", "tokens", "type", "usage",
}


def _old_key_words(key: str) -> list[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return [word for word in re.split(r"[^a-z0-9]+", snake.lower()) if word]


def _the_old_decision(key: str, redact_keys: set[str]) -> bool:
    words = _old_key_words(key)
    if not words or words[-1] in _NON_SECRET:
        return False
    joined = f"_{'_'.join(words)}_"
    compact = "".join(words)
    for pattern in redact_keys:
        pattern_words = _old_key_words(pattern)
        if not pattern_words:
            continue
        if f"_{'_'.join(pattern_words)}_" in joined or compact.endswith(
            "".join(pattern_words)
        ):
            return True
    return False


KEYS = [
    "api_key", "apiKey", "API-KEY", "x_api_key", "client_secret", "clientSecret",
    "sessiontoken", "session_token", "access_token", "refreshToken", "password",
    "authorization", "Authorization", "bearer", "private_key", "signature",
    # not secrets: a quantity, unit, or category, or a fragment
    "max_tokens", "prompt_token_count", "token_type", "time_to_first_delta_ms",
    "tokenizer", "tokens", "token_usage", "key_count", "secret_bytes",
    "keyboard", "passwords_limit", "id", "name", "model", "content", "",
    "with spaces", "UPPER_LOWER_Mixed", "dots.and-dashes_here", "123",
]


def test_every_decision_is_what_it_was_before():
    patterns = {key.lower() for key in TelemetryConfig().redact_keys}

    for key in KEYS:
        assert _should_redact_key(key, patterns) == _the_old_decision(key, patterns), key


def test_a_payload_is_redacted_as_before():
    payload = {
        "api_key": "sk-1", "max_tokens": 5, "nested": {"clientSecret": "x", "count": 2},
        "list": [{"sessiontoken": "t", "tokenizer": "cl100k"}],
    }

    redacted = redact_payload(payload, TelemetryConfig())

    assert redacted["api_key"] == REDACTION_MARKER
    assert redacted["max_tokens"] == 5
    assert redacted["nested"] == {"clientSecret": REDACTION_MARKER, "count": 2}
    assert redacted["list"] == [{"sessiontoken": REDACTION_MARKER, "tokenizer": "cl100k"}]


def test_a_key_is_split_into_words_once_however_often_it_is_seen():
    """The guard: the regex work happens per distinct key, not per decision."""
    calls = 0
    original = redaction._split_key_words

    def counted(key):
        nonlocal calls
        calls += 1
        return original(key)

    redaction._split_key_words = counted
    try:
        redaction._key_words.cache_clear()
        redaction._decide_key.cache_clear()
        config = TelemetryConfig()
        payload = {f"field_{index % 20}": {"api_key": "x", "count": index} for index in range(400)}
        redact_payload(payload, config)
        redact_payload(payload, config)
    finally:
        redaction._split_key_words = original

    distinct = 20 + 2 + len(config.redact_keys)  # keys seen, plus every pattern
    assert calls <= distinct, f"split words {calls} times for {distinct} distinct keys"


def test_changing_the_patterns_changes_the_decision():
    """Remembering a decision must not outlive the patterns it was made for."""
    assert _should_redact_key("greeting", {"api_key"}) is False
    assert _should_redact_key("greeting", {"greeting"}) is True
    assert _should_redact_key("greeting", {"api_key"}) is False
