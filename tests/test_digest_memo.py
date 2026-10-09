"""A message's privacy-safe form is worked out once per trace (R4 of the P6).

Every step digested the whole context again: each message was redacted,
scrubbed and masked three times over (for the context events, for the model
call's evidence, and once more inside each message's own digest), and the
canonical form of unchanged content is the same each time. The support desk
ramp (2026-10-07) put that at about 10% of the event loop, and the context
evidence at 9%. The form is now kept for the length of the trace, and
``context_evidence`` works each message out once. What the trace records is
byte for byte what it was; these tests hold that, and hold the places the
memo must not outlive: a credential registered later, a changed policy.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core import credentials
from omnicoreagent.core.interaction_history import context_evidence, stable_message_digest
from omnicoreagent.core.telemetry.recorder import TelemetryRecorder
from omnicoreagent.core.telemetry.store import InMemoryTelemetryStore

MESSAGES = [
    {"role": "system", "content": "You are the support desk assistant. " * 8},
    {"role": "user", "content": "Refund 1042 please; my email is maya.chen@example.com " * 4},
    {
        "role": "assistant",
        "content": "",
        "metadata": {"tool_calls": [{"id": "c1", "function": {"name": "lookup_order", "arguments": "{}"}}]},
    },
    {"role": "tool", "tool_call_id": "c1", "content": '{"status": "success", "data": {"api_key": "abc123"}}'},
]
TOOLS = [{"type": "function", "function": {"name": "lookup_order", "description": "Look up an order."}}]


async def _in_trace(recorder, body):
    await recorder.start_trace(name="t")
    try:
        return await body()
    finally:
        await recorder.end_trace()


@pytest.mark.asyncio
async def test_the_digests_of_a_context_are_the_same_with_and_without_the_memo():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    expected = context_evidence(MESSAGES, TOOLS, canonicalizer=recorder._canonicalize)

    async def body():
        first = context_evidence(MESSAGES, TOOLS, canonicalizer=recorder.canonicalize_for_digest)
        again = context_evidence(MESSAGES, TOOLS, canonicalizer=recorder.canonicalize_for_digest)
        return first, again

    first, again = await _in_trace(recorder, body)
    assert first == again == expected
    assert [stable_message_digest(m, canonicalizer=recorder._canonicalize) for m in MESSAGES] == expected["message_digests"]


@pytest.mark.asyncio
async def test_content_seen_before_is_not_worked_out_again_within_a_trace():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    worked = []
    original = recorder._canonicalize
    recorder._canonicalize = lambda value: (worked.append(1), original(value))[1]

    async def body():
        context_evidence(MESSAGES, TOOLS, canonicalizer=recorder.canonicalize_for_digest)
        first = len(worked)
        context_evidence(MESSAGES, TOOLS, canonicalizer=recorder.canonicalize_for_digest)
        context_evidence(
            [*MESSAGES, {"role": "assistant", "content": "done"}],
            TOOLS,
            canonicalizer=recorder.canonicalize_for_digest,
        )
        return first

    first = await _in_trace(recorder, body)
    # The second look worked out nothing; the third only the new message.
    assert len(worked) == first + 1


@pytest.mark.asyncio
async def test_a_message_is_canonicalized_once_by_context_evidence_not_twice():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    calls = []
    original = recorder._canonicalize
    recorder._canonicalize = lambda value: (calls.append(1), original(value))[1]
    # No trace is current here, so there is no memo: this counts the work itself.
    context_evidence(MESSAGES, TOOLS, canonicalizer=recorder.canonicalize_for_digest)
    assert len(calls) == len(MESSAGES) + 1  # each message once, the tool list once


@pytest.mark.asyncio
async def test_a_credential_registered_after_the_memo_is_scrubbed_from_what_it_returns():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    secret = "sk-test-registered-later-9f8e7d6c5b4a"
    message = {"role": "user", "content": f"my key is {secret} " * 10}

    async def body():
        before = recorder.canonicalize_for_digest(message)
        assert secret in before["content"]
        credentials.register_credential(secret)
        return recorder.canonicalize_for_digest(message)

    try:
        after = await _in_trace(recorder, body)
    finally:
        credentials._values = frozenset()
        credentials._ordered = ()
    assert secret not in after["content"]


@pytest.mark.asyncio
async def test_the_memo_is_bounded_and_ends_with_the_trace():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    context = await recorder.start_trace(name="t")
    for number in range(recorder._CANONICAL_ENTRIES + 50):
        recorder.canonicalize_for_digest({"role": "user", "content": f"message {number}"})
    recording = recorder.context_recording()
    assert len(recording.canonical) <= recorder._CANONICAL_ENTRIES
    assert recorder.context_recording() is recording
    await recorder.end_trace()
    assert context.trace_id not in recorder._context_recordings
