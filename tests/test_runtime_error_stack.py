"""A runtime error recorded at full capture carries its traceback.

Found deploying the repository steward (P1): a run died before its first
model call, the trace's `runtime_error` event named the exception and its
message and nothing else, and the traceback had to be reproduced locally.
At ``capture="full"`` the error's stack is recorded; under the privacy-first
default it stays out, like every other payload.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.telemetry import ActorType, InMemoryTelemetryStore, TelemetryActor, TelemetryRecorder
from omnicoreagent.core.telemetry.redaction import TelemetryConfig


def _explode():
    raise ValueError("the digest choked on a schema")


async def _recorded_error(config: TelemetryConfig):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, config)
    context = await recorder.start_trace(
        trace_id="trace-err", run_id="run-err", actor=TelemetryActor(type=ActorType.SYSTEM, name="t")
    )
    try:
        _explode()
    except ValueError as exc:
        await recorder.record_exception(exc)
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    return next(e for e in trace.events if e.event_type == "runtime_error").error


@pytest.mark.asyncio
async def test_full_capture_records_the_traceback():
    error = await _recorded_error(TelemetryConfig(capture="full"))
    assert (error.type, error.message) == ("ValueError", "the digest choked on a schema")
    assert error.stack and "_explode" in error.stack and "Traceback" in error.stack


@pytest.mark.asyncio
async def test_the_privacy_first_capture_keeps_the_traceback_out():
    error = await _recorded_error(TelemetryConfig(capture="default"))
    assert (error.type, error.message) == ("ValueError", "the digest choked on a schema")
    assert error.stack is None
