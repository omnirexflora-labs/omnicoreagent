"""The event loop does not do the same work twice (R4 of the support desk P6).

The support desk ramp (2026-10-07) profiled the event loop: privacy redaction
was 14-28% of its time (the same message redacted again at every boundary and
every step), token counting 12% (the whole context counted again each step),
and the digest of unchanged content computed again. Each of these is a pure
function of its input, so the answer for content already seen is kept, by a
digest of the content and never by the text itself, in a bounded cache. These
tests hold three things: the answers are the same as without the cache, the
second look does not repeat the work, and the cache cannot grow without bound
or hold the text it was asked about.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.summarizer import tokenizer

PII = (
    "Write to maya.chen@example.com or call +1 (415) 555-0132; her card is "
    "4111 1111 1111 1111 and her SSN is 123-45-6789. Order 1042 shipped on 2026-09-18, "
    "trace_cbe5ba4111111111, chatcmpl-7fe09b14-1234-5678-9012-123456789abc, total 0.4111111111111."
)
CORPUS = [
    PII,
    "plain text with nothing to hide " * 12,
    {"content": PII, "id": "4111111111111111", "nested": [PII, {"signature": "123-45-6789"}]},
    ["a" * 500, PII + PII],
    "short",
    "",
]


EVERYWHERE = {
    "redact_memory": True,
    "redact_workspace": True,
    "redact_stream": True,
    "redact_public": True,
    "redact_model_io": True,
}


class _CountingFilter(PrivacyFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scans = 0

    def _scan(self, value, boundary):
        self.scans += 1
        return super()._scan(value, boundary)


def test_a_cached_redaction_gives_the_answer_an_uncached_one_gives():
    cached, fresh = PrivacyFilter(EVERYWHERE), PrivacyFilter(EVERYWHERE)
    for boundary in ("model", "memory", "telemetry", "public"):
        for value in CORPUS:
            first = cached.redact(value, boundary=boundary)
            again = cached.redact(value, boundary=boundary)
            assert first == again == fresh.redact(value, boundary=boundary)


def test_text_already_redacted_at_a_boundary_is_not_scanned_again():
    privacy = _CountingFilter(EVERYWHERE)
    privacy.redact_text(PII, boundary="memory")
    scans = privacy.scans
    assert scans >= 1

    for _ in range(5):
        assert "maya.chen@example.com" not in privacy.redact_text(PII, boundary="memory")
    assert privacy.scans == scans

    # Whether a boundary redacts at all is decided before the scan, and what
    # the scan finds does not depend on the boundary: one answer serves all.
    privacy.redact_text(PII, boundary="telemetry")
    assert privacy.scans == scans


def test_a_boundary_that_does_not_redact_is_never_scanned():
    privacy = _CountingFilter({})  # by default only telemetry redacts
    assert privacy.redact_text(PII, boundary="memory") == PII
    assert privacy.scans == 0


def test_the_redaction_cache_is_bounded_and_keeps_digests_not_text():
    privacy = PrivacyFilter(EVERYWHERE)
    limit = privacy._CACHE_ENTRIES
    for number in range(limit + 50):
        privacy.redact_text(f"customer {number} wrote to person{number}@example.com " * 3, boundary="memory")
    assert len(privacy._cache) <= limit
    for key in privacy._cache:
        assert isinstance(key, bytes) and len(key) == 16, "keyed by a digest of the text"


def test_a_filter_whose_config_is_replaced_does_not_answer_from_the_old_one():
    privacy = PrivacyFilter(EVERYWHERE)
    assert "[REDACTED_EMAIL]" in privacy.redact_text(PII, boundary="memory")
    privacy.config = PrivacyFilter({"categories": ["ssn"], **EVERYWHERE}).config
    later = privacy.redact_text(PII, boundary="memory")
    assert "maya.chen@example.com" in later and "123-45-6789" not in later


class _Encoding:
    def __init__(self):
        self.calls = 0

    def encode(self, text):
        self.calls += 1
        return text.split()


@pytest.fixture
def encoding(monkeypatch):
    fake = _Encoding()
    monkeypatch.setattr(tokenizer, "get_encoding", lambda model="gpt-4": fake)
    tokenizer._count_cache.clear()
    return fake


def test_a_message_counted_before_is_not_encoded_again(encoding):
    text = "the quick brown fox jumps over the lazy dog " * 40
    first = tokenizer.count_tokens(text)
    for _ in range(3):
        assert tokenizer.count_tokens(text) == first
    assert encoding.calls == 1
    # A new message is encoded; the old ones are not.
    tokenizer.count_tokens(text + "and one more")
    assert encoding.calls == 2


def test_the_count_for_a_model_is_its_own(encoding):
    text = "word " * 100
    tokenizer.count_tokens(text, "gpt-4")
    tokenizer.count_tokens(text, "other-model")
    assert encoding.calls == 2


def test_the_token_cache_is_bounded(encoding):
    for number in range(tokenizer._COUNT_CACHE_ENTRIES + 20):
        tokenizer.count_tokens(f"message {number} " * 30)
    assert len(tokenizer._count_cache) <= tokenizer._COUNT_CACHE_ENTRIES
