"""Audit A7: a streamed delta is redacted once, where the text is.

Profiled: with a 2,000-delta answer, privacy redaction was 27 of 28.6 seconds
under the profiler. Every delta's whole envelope was walked — its type, phase,
identifiers, agent name — and each string ran the four PII patterns, about
five times per one-character delta. Only the delta's text can carry PII; the
envelope is identifiers by construction. Redaction still happens, once, on
the text.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.runtime.streaming import StreamDelivery


async def _delivered(delivery: StreamDelivery, event: dict) -> dict:
    received: list[dict] = []

    async def callback(payload):
        received.append(payload)

    delivery.callback = callback
    await delivery.emit(
        event, agent_name="probe", run_id="run_1", session_id="s", trace_id="t"
    )
    [payload] = received
    return payload


@pytest.mark.asyncio
async def test_a_delta_with_personal_data_in_it_is_still_redacted():
    delivery = StreamDelivery(callback=None, run_id="run_1", privacy_filter=PrivacyFilter())

    payload = await _delivered(
        delivery, {"type": "text_delta", "text": "write to ada@example.com today"}
    )

    assert "ada@example.com" not in payload["text"]
    assert payload["type"] == "text_delta"


@pytest.mark.asyncio
async def test_redacting_a_delta_runs_the_patterns_once_on_its_text():
    filter_ = PrivacyFilter()
    delivery = StreamDelivery(callback=None, run_id="run_1", privacy_filter=filter_)
    calls = []
    original = filter_.redact_text

    def counted(value, *, boundary):
        calls.append(value)
        return original(value, boundary=boundary)

    filter_.redact_text = counted
    await _delivered(delivery, {"type": "text_delta", "text": "hello"})

    assert calls == ["hello"], f"redacted {calls} for one delta"


@pytest.mark.asyncio
async def test_the_envelope_around_a_delta_is_left_as_it_is():
    """Identifiers and phases are never personal data; a phone-shaped run id
    must not be rewritten into a marker."""
    delivery = StreamDelivery(
        callback=None, run_id="run 555-0100-1234-567", privacy_filter=PrivacyFilter()
    )

    payload = await _delivered(delivery, {"type": "text_delta", "text": "hi"})

    assert payload["run_id"] == "run 555-0100-1234-567"
    assert payload["phase"] == "intermediate" and payload["agent_name"] == "probe"
    assert payload["event_id"].endswith(":text:1")
