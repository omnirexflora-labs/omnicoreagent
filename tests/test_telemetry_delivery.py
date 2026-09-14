from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
    TelemetryTrace,
    TraceStatus,
)


class SlowEventStore(InMemoryTelemetryStore):
    async def append_event(self, trace_id, event):
        await asyncio.sleep(0.05)
        await super().append_event(trace_id, event)


class SlowExporter:
    name = "slow"

    async def export_trace(self, trace: TelemetryTrace):
        await asyncio.sleep(0.05)
        raise AssertionError("the timeout should cancel this exporter")


@pytest.mark.asyncio
async def test_best_effort_store_timeout_marks_trace_partial_and_finishes():
    store = SlowEventStore()
    recorder = TelemetryRecorder(
        store,
        config=TelemetryConfig(persistence_timeout_seconds=0.001),
    )

    await recorder.start_trace(trace_id="trace-store-timeout")
    await recorder.emit_event("user_message", input={"message": "hello"})
    await recorder.end_trace()

    trace = await store.get_trace("trace-store-timeout")
    assert trace is not None
    assert trace.status == TraceStatus.COMPLETED
    assert trace.incomplete is True
    assert trace.evidence_status == "partial"


@pytest.mark.asyncio
async def test_best_effort_exporter_timeout_is_visible_without_failing_run():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(
        store,
        config=TelemetryConfig(export_timeout_seconds=0.001),
        exporters=[SlowExporter()],
    )

    await recorder.start_trace(trace_id="trace-export-timeout")
    await recorder.end_trace()

    trace = await store.get_trace("trace-export-timeout")
    assert trace is not None
    assert trace.status == TraceStatus.COMPLETED
    failures = [event for event in trace.events if event.event_type == "telemetry_error"]
    assert len(failures) == 1
    assert failures[0].metadata["component"] == "exporter"
    assert failures[0].metadata["exporter"] == "slow"


@pytest.mark.asyncio
async def test_strict_exporter_timeout_fails_finalization():
    recorder = TelemetryRecorder(
        InMemoryTelemetryStore(),
        config=TelemetryConfig(strict=True, export_timeout_seconds=0.001),
        exporters=[SlowExporter()],
    )

    await recorder.start_trace(trace_id="trace-strict-export-timeout")
    with pytest.raises(asyncio.TimeoutError):
        await recorder.end_trace()


def test_delivery_timeout_configuration_rejects_invalid_values():
    with pytest.raises(ValueError, match="persistence_timeout_seconds"):
        TelemetryConfig(persistence_timeout_seconds=-1)
    with pytest.raises(ValueError, match="export_timeout_seconds"):
        TelemetryConfig(export_timeout_seconds=float("nan"))
