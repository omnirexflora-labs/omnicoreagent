import asyncio
from datetime import timedelta

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    CaptureState,
    InMemoryTelemetryExporter,
    InMemoryTelemetryStore,
    JsonlTelemetryStore,
    SpanStatus,
    TelemetryActor,
    TelemetryCapture,
    TelemetryConfig,
    TelemetryEvent,
    TelemetryNormalizer,
    TelemetryRecorder,
    TelemetrySpan,
    TelemetryStream,
    TelemetryStreamScope,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TelemetryProvenance,
    OTelTraceMapper,
    TraceFilter,
    TraceEvidenceStatus,
    TraceStatus,
    current_telemetry_context,
)
from omnicoreagent.core.telemetry.models import utc_now
from omnicoreagent.core.privacy import PrivacyFilter


def test_telemetry_config_fingerprint_is_stable_and_policy_sensitive():
    first = TelemetryConfig(redact_keys=["token", "api_key"])
    second = TelemetryConfig(redact_keys=["api_key", "token"])
    changed = TelemetryConfig(record_model_responses=True)

    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


def test_telemetry_storage_policy_validates_and_omits_path_from_fingerprint():
    first = TelemetryConfig(storage="jsonl", storage_path="/one/traces.jsonl")
    second = TelemetryConfig(storage="jsonl", storage_path="/two/traces.jsonl")

    assert first.fingerprint() == second.fingerprint()
    with pytest.raises(ValueError, match="storage must be one of"):
        TelemetryConfig(storage="otlp")
    with pytest.raises(ValueError, match="retention_days"):
        TelemetryConfig(retention_days=-1)


def test_telemetry_trace_metadata_round_trips_telemetry_config_version():
    metadata = TelemetryTraceMetadata(
        telemetry_config_version="abc123",
        telemetry_storage="jsonl",
        telemetry_payload_storage="workspace",
        privacy_config_version="privacy123",
    )

    restored = TelemetryTraceMetadata.from_dict(metadata.model_dump())

    assert restored.telemetry_config_version == "abc123"
    assert restored.telemetry_storage == "jsonl"
    assert restored.telemetry_payload_storage == "workspace"
    assert restored.privacy_config_version == "privacy123"


def test_evidence_records_are_versioned_and_provenance_round_trips():
    trace = TelemetryTrace(
        trace_id="trace-v3",
        root_span_id="span-v3",
        execution_surface="controlled",
        provenance=TelemetryProvenance(
            adapter="harbor",
            evaluation_id="eval-1",
            case_id="case-1",
            trial_id="trial-1",
        ),
    )

    restored = TelemetryTrace.from_dict(trace.model_dump())

    assert trace.schema_version == 3
    assert trace.evidence_status == TraceEvidenceStatus.COMPLETE
    assert restored.execution_surface == "controlled"
    assert restored.provenance.adapter == "harbor"
    assert restored.provenance.trial_id == "trial-1"


def test_legacy_evidence_records_are_marked_unknown_by_normalizer():
    legacy = {
        "trace_id": "trace-legacy",
        "root_span_id": "span-legacy",
        "status": "completed",
        "ended_at": utc_now().isoformat(),
        "spans": [],
        "events": [],
    }

    normalized = TelemetryNormalizer().normalize(TelemetryTrace.from_dict(legacy))

    assert normalized.schema_version == 1
    assert normalized.evidence_status == TraceEvidenceStatus.PARTIAL
    assert "legacy_schema" in normalized.metadata.tags
    assert "missing_evidence" in normalized.metadata.tags


def test_capture_descriptor_requires_reasons_for_unavailable_payloads():
    available = TelemetryCapture(
        state=CaptureState.AVAILABLE,
        source="runtime",
        role="request",
    )
    assert available.state == CaptureState.AVAILABLE

    with pytest.raises(ValueError, match="reason is required"):
        TelemetryCapture(
            state=CaptureState.NOT_RECORDED,
            source="runtime",
            role="request",
        )


def test_telemetry_records_serialize_and_validate():
    actor = TelemetryActor(type=ActorType.AGENT, name="assistant")
    span = TelemetrySpan(
        trace_id="trace-1",
        span_id="span-1",
        name="agent.run",
        kind="agent.run",
        actor=actor,
    )
    event = TelemetryEvent(
        trace_id="trace-1",
        span_id="span-1",
        event_type="agent_start",
        actor=actor,
        input={"message": "hello"},
    )
    trace = TelemetryTrace(
        trace_id="trace-1",
        root_span_id="span-1",
        metadata=TelemetryTraceMetadata(agent_name="assistant"),
        spans=[span],
        events=[event],
    )

    dumped = trace.model_dump()
    restored = TelemetryTrace.from_dict(dumped)

    assert restored.trace_id == "trace-1"
    assert restored.incomplete is False
    assert restored.spans[0].kind == "agent.run"
    assert restored.events[0].event_type == "agent_start"
    assert restored.events[0].token_usage.total_tokens is None


def test_unknown_event_type_requires_experimental_metadata():
    with pytest.raises(ValueError, match="Unknown telemetry event type"):
        TelemetryEvent(
            trace_id="trace-1",
            event_type="custom_event",
            actor=TelemetryActor(type=ActorType.SYSTEM),
        )

    event = TelemetryEvent(
        trace_id="trace-1",
        event_type="custom_event",
        actor=TelemetryActor(type=ActorType.SYSTEM),
        metadata={"experimental": True},
    )

    assert event.event_type == "custom_event"


@pytest.mark.asyncio
async def test_recorder_captures_trace_span_event_and_redacts_payload():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(
        store,
        TelemetryConfig(redact_keys=["api_key"], max_payload_bytes=500),
    )

    context = await recorder.start_trace(
        trace_id="trace-redact",
        run_id="run-1",
        session_id="session-1",
        actor=TelemetryActor(type=ActorType.AGENT, name="assistant"),
        input={"api_key": "secret", "safe": "value"},
    )
    await recorder.emit_event(
        "user_message",
        actor=TelemetryActor(type=ActorType.USER),
        input={"api_key": "secret", "message": "hello"},
    )
    async with recorder.span(name="model.call", kind="model.call"):
        await recorder.emit_event("model_call", input={"prompt": "hi"})
    await recorder.end_trace(output={"answer": "done"})

    trace = await store.get_trace(context.trace_id)

    assert trace is not None
    assert trace.status == TraceStatus.COMPLETED
    assert trace.run_id == "run-1"
    assert trace.session_id == "session-1"
    assert trace.spans[0].input == {"api_key": "[REDACTED]", "safe": "value"}
    assert trace.events[0].input == {"api_key": "[REDACTED]", "message": "hello"}
    assert trace.spans[0].input_capture.state == CaptureState.REDACTED
    assert trace.events[0].input_capture.state == CaptureState.REDACTED
    assert trace.spans[-1].status == SpanStatus.OK
    assert current_telemetry_context() is None


@pytest.mark.asyncio
async def test_recorder_marks_trace_evidence_partial_when_capture_is_disabled():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(record_model_responses=False))
    context = await recorder.start_trace(trace_id="trace-capture-gap")
    await recorder.emit_event(
        "model_response", output={"content": "private response"}
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL
    assert trace.events[-1].output_capture.state == CaptureState.NOT_RECORDED

    normalized = TelemetryNormalizer().normalize(trace)
    assert normalized.evidence_status == TraceEvidenceStatus.PARTIAL
    assert "capture_gaps" in normalized.metadata.tags
    assert any(
        event.metadata.get("normalizer") == "capture_gaps"
        for event in normalized.events
    )


@pytest.mark.asyncio
async def test_recorder_context_propagates_to_parallel_tasks():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-parallel")

    async def worker(index: int):
        async with recorder.span(
            name=f"tool-{index}",
            kind="tool.call",
            actor=TelemetryActor(type=ActorType.TOOL, name=f"tool-{index}"),
        ):
            await recorder.emit_event("tool_call", input={"index": index})

    await asyncio.gather(worker(1), worker(2))

    trace = await store.get_trace("trace-parallel")
    tool_spans = [span for span in trace.spans if span.kind == "tool.call"]

    assert len(tool_spans) == 2
    assert {span.parent_span_id for span in tool_spans} == {trace.root_span_id}
    assert sorted(event.input["index"] for event in trace.events) == [1, 2]


@pytest.mark.asyncio
async def test_telemetry_stream_replays_and_follows_by_scope():
    store = InMemoryTelemetryStore()
    stream = TelemetryStream(store)
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(
        trace_id="trace-stream",
        run_id="run-stream",
        session_id="session-stream",
    )
    scope = TelemetryStreamScope(session_id="session-stream")
    cursor = await stream.get_stream_cursor(scope)
    await recorder.emit_event("agent_start")

    replayed = await stream.get_events_after(scope, cursor)

    assert [event.event_type for event in replayed] == ["agent_start"]

    live = stream.stream_after(scope, await stream.get_stream_cursor(scope))
    next_event = asyncio.create_task(anext(live))
    await recorder.emit_event("agent_step")
    event = await asyncio.wait_for(next_event, timeout=1)
    await live.aclose()

    assert event.event_type == "agent_step"


@pytest.mark.asyncio
async def test_stream_replay_exposes_transport_cursor_without_changing_trace_identity():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-stream-cursor", session_id="session-cursor")
    await recorder.emit_event("agent_start")
    await recorder.emit_event("agent_step", input={"step": 1})

    events = await store.get_events_after(
        TelemetryStreamScope(session_id="session-cursor"), None
    )
    trace = await store.get_trace("trace-stream-cursor")

    assert [event.stream_cursor for event in events] == ["1", "2"]
    assert [event.event_id for event in events] == [event.event_id for event in trace.events]
    assert all(event.stream_cursor is None for event in trace.events)


@pytest.mark.asyncio
async def test_stream_rejects_invalid_cursor():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-invalid-cursor")

    with pytest.raises(ValueError, match="non-negative integer"):
        await store.get_events_after(
            TelemetryStreamScope(trace_id="trace-invalid-cursor"), "event-id"
        )


@pytest.mark.asyncio
async def test_telemetry_stream_isolates_sessions():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-a", session_id="session-a")
    await recorder.emit_event("agent_start")
    await recorder.start_trace(trace_id="trace-b", session_id="session-b")
    await recorder.emit_event("agent_start")

    events = await store.get_events_after(
        TelemetryStreamScope(session_id="session-a"),
        None,
    )

    assert len(events) == 1
    assert events[0].trace_id == "trace-a"


@pytest.mark.asyncio
async def test_live_telemetry_stream_reports_queue_overflow():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-overflow", session_id="session-overflow")

    scope = TelemetryStreamScope(session_id="session-overflow")
    live = store.stream_after(scope, await store.get_stream_cursor(scope))
    first_event = asyncio.create_task(anext(live))
    await asyncio.sleep(0)

    writes = [
        asyncio.create_task(
            recorder.emit_event("agent_step", input={"index": index})
        )
        for index in range(1002)
    ]
    await asyncio.gather(*writes)
    await first_event

    with pytest.raises(RuntimeError, match="queue overflow"):
        for _ in range(1002):
            await asyncio.wait_for(anext(live), timeout=1)
    await live.aclose()


@pytest.mark.asyncio
async def test_jsonl_store_persists_trace_events_and_spans(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path)
    recorder = TelemetryRecorder(store)

    await recorder.start_trace(trace_id="trace-jsonl", session_id="session-jsonl")
    await recorder.emit_event("agent_start")
    await recorder.end_trace()

    reloaded = JsonlTelemetryStore(path)
    trace = await reloaded.get_trace("trace-jsonl")

    assert trace is not None
    assert trace.trace_id == "trace-jsonl"
    assert trace.status == TraceStatus.COMPLETED
    assert [event.event_type for event in trace.events] == ["agent_start"]

    replayed = await reloaded.get_events_after(
        TelemetryStreamScope(session_id="session-jsonl"), None
    )
    assert replayed[0].stream_cursor == "1"


@pytest.mark.asyncio
async def test_jsonl_store_prunes_old_ended_traces_and_keeps_active_traces(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    writer = JsonlTelemetryStore(path)

    old = TelemetryTrace(
        trace_id="trace-old",
        root_span_id="span-old",
        status=TraceStatus.COMPLETED,
        started_at=utc_now() - timedelta(days=3),
        ended_at=utc_now() - timedelta(days=2),
    )
    recent = TelemetryTrace(
        trace_id="trace-recent",
        root_span_id="span-recent",
        status=TraceStatus.COMPLETED,
        started_at=utc_now(),
        ended_at=utc_now(),
    )
    active = TelemetryTrace(
        trace_id="trace-active",
        root_span_id="span-active",
        status=TraceStatus.RUNNING,
        started_at=utc_now() - timedelta(days=30),
    )
    await writer.upsert_trace(old)
    await writer.upsert_trace(recent)
    await writer.upsert_trace(active)
    await writer.flush()  # written in batches: on disk before another store reads it

    reloaded = JsonlTelemetryStore(path)
    removed = await reloaded.prune(retention_days=1)

    assert removed == 1
    assert await reloaded.get_trace("trace-old") is None
    assert await reloaded.get_trace("trace-recent") is not None
    assert await reloaded.get_trace("trace-active") is not None
    assert "trace-old" not in path.read_text()


@pytest.mark.asyncio
async def test_jsonl_store_reloads_stream_scope_by_run_id(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path)
    first = TelemetryRecorder(store)
    await first.start_trace(
        trace_id="trace-jsonl-first",
        session_id="session-jsonl-shared",
        run_id="run-jsonl-first",
    )
    await first.emit_event("user_message", input={"message": "first"})
    await first.end_trace()
    second = TelemetryRecorder(store)
    await second.start_trace(
        trace_id="trace-jsonl-second",
        session_id="session-jsonl-shared",
        run_id="run-jsonl-second",
    )
    await second.emit_event("user_message", input={"message": "second"})
    await second.end_trace()

    reloaded = JsonlTelemetryStore(path)
    events = await reloaded.get_events_after(
        TelemetryStreamScope(
            session_id="session-jsonl-shared",
            run_id="run-jsonl-second",
        ),
        None,
    )

    assert [event.trace_id for event in events] == ["trace-jsonl-second"]
    assert [event.input for event in events] == [{"message": "second"}]


@pytest.mark.asyncio
async def test_store_filters_traces():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(
        trace_id="trace-filter",
        session_id="session-filter",
        metadata=TelemetryTraceMetadata(model="gpt-5.4-mini"),
    )
    await recorder.end_trace()

    traces = await store.list_traces(
        TraceFilter(session_id="session-filter", model="gpt-5.4-mini")
    )

    assert [trace.trace_id for trace in traces] == ["trace-filter"]


def test_normalizer_sorts_and_marks_missing_evidence():
    trace = TelemetryTrace(
        trace_id="trace-normalize",
        root_span_id="missing-root",
        spans=[],
        events=[
            TelemetryEvent(
                trace_id="trace-normalize",
                event_type="agent_end",
                sequence_number=2,
                actor=TelemetryActor(type=ActorType.AGENT),
            ),
            TelemetryEvent(
                trace_id="trace-normalize",
                event_type="agent_start",
                sequence_number=1,
                actor=TelemetryActor(type=ActorType.AGENT),
            ),
        ],
    )

    normalized = TelemetryNormalizer().normalize(trace)

    assert [event.event_type for event in normalized.events[:2]] == [
        "agent_start",
        "agent_end",
    ]
    assert "missing_evidence" in normalized.metadata.tags
    assert any(
        event.metadata == {"normalizer": "missing_evidence"}
        for event in normalized.events
    )


def test_normalizer_is_idempotent_for_missing_and_incomplete_trace():
    trace = TelemetryTrace(trace_id="trace-idempotent", root_span_id="missing")
    normalizer = TelemetryNormalizer()

    first = normalizer.normalize(trace)
    second = normalizer.normalize(first)

    assert len(second.events) == len(first.events)
    assert "missing_evidence" in second.metadata.tags
    assert "incomplete_trace" in second.metadata.tags


def test_normalizer_uses_stable_synthetic_event_ids_for_same_raw_trace():
    trace = TelemetryTrace(trace_id="trace-stable-normalize", root_span_id="missing")
    normalizer = TelemetryNormalizer()

    first = normalizer.normalize(trace)
    second = normalizer.normalize(trace)

    assert first.model_dump() == second.model_dump()
    assert {
        event.event_id
        for event in first.events
        if event.metadata.get("normalizer")
    } == {
        "event_normalizer_trace-stable-normalize_missing_evidence",
        "event_normalizer_trace-stable-normalize_incomplete_trace",
    }


def test_normalizer_avoids_synthetic_event_id_collision_with_raw_events():
    trace = TelemetryTrace(
        trace_id="trace-collision",
        root_span_id="missing",
        events=[
            TelemetryEvent(
                trace_id="trace-collision",
                event_id="event_normalizer_trace-collision_missing_evidence",
                event_type="runtime_error",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                metadata={"experimental": True},
            )
        ],
    )

    normalized = TelemetryNormalizer().normalize(trace)
    event_ids = [event.event_id for event in normalized.events]

    assert len(event_ids) == len(set(event_ids))
    assert "event_normalizer_trace-collision_missing_evidence" in event_ids
    assert "event_normalizer_trace-collision_missing_evidence_1" in event_ids


def test_normalizer_preserves_errors_status_and_sorts_event_ids():
    span = TelemetrySpan(
        trace_id="trace-preserve",
        span_id="span-root",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT),
        status=SpanStatus.ERROR,
        error={"type": "RuntimeError", "message": "boom"},
        event_ids=["event-2", "event-1", "event-2"],
    )
    trace = TelemetryTrace(
        trace_id="trace-preserve",
        root_span_id="span-root",
        status=TraceStatus.FAILED,
        spans=[span],
        events=[
            TelemetryEvent(
                trace_id="trace-preserve",
                event_id="event-2",
                span_id="span-root",
                sequence_number=2,
                event_type="runtime_error",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                error={"type": "RuntimeError", "message": "boom"},
            ),
            TelemetryEvent(
                trace_id="trace-preserve",
                event_id="event-1",
                span_id="span-root",
                sequence_number=1,
                event_type="user_message",
                actor=TelemetryActor(type=ActorType.USER),
            ),
        ],
    )

    normalized = TelemetryNormalizer().normalize(trace)

    assert normalized.status == TraceStatus.FAILED
    assert normalized.spans[0].status == SpanStatus.ERROR
    assert normalized.spans[0].error.type == "RuntimeError"
    assert normalized.spans[0].event_ids == ["event-1", "event-2"]
    assert [event.event_id for event in normalized.events[:2]] == [
        "event-1",
        "event-2",
    ]
    assert normalized.events[-1].event_id == (
        "event_normalizer_trace-preserve_incomplete_trace"
    )
    assert normalized.events[1].error.type == "RuntimeError"


@pytest.mark.asyncio
async def test_recorder_strict_mode_raises_store_errors():
    class BrokenStore(InMemoryTelemetryStore):
        async def upsert_trace(self, trace):
            raise RuntimeError("boom")

    recorder = TelemetryRecorder(BrokenStore(), TelemetryConfig(strict=True))

    with pytest.raises(RuntimeError, match="boom"):
        await recorder.start_trace(trace_id="trace-strict")


@pytest.mark.asyncio
async def test_recorder_best_effort_suppresses_store_errors():
    class BrokenStore(InMemoryTelemetryStore):
        async def upsert_trace(self, trace):
            raise RuntimeError("boom")

    recorder = TelemetryRecorder(BrokenStore(), TelemetryConfig(strict=False))

    context = await recorder.start_trace(trace_id="trace-best-effort")

    assert context.trace_id == "trace-best-effort"


@pytest.mark.asyncio
async def test_recorder_marks_trace_incomplete_after_best_effort_write_failure():
    class FlakyStore(InMemoryTelemetryStore):
        fail_next_event = True

        async def append_event(self, trace_id, event):
            if self.fail_next_event:
                self.fail_next_event = False
                raise RuntimeError("event write failed")
            await super().append_event(trace_id, event)

    store = FlakyStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(strict=False))

    await recorder.start_trace(trace_id="trace-incomplete")
    await recorder.emit_event("agent_start")
    await recorder.end_trace()

    trace = await store.get_trace("trace-incomplete")
    assert trace is not None
    assert trace.status == TraceStatus.COMPLETED
    assert trace.incomplete is True
    assert current_telemetry_context() is None


@pytest.mark.asyncio
async def test_direct_span_end_restores_parent_context_for_siblings():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-siblings")
    first = await recorder.start_span(name="first", kind="tool.call")
    await recorder.end_span(first.span_id)
    await recorder.start_span(name="second", kind="tool.call")

    trace = await store.get_trace("trace-siblings")
    first_span = next(span for span in trace.spans if span.name == "first")
    second_span = next(span for span in trace.spans if span.name == "second")

    assert first_span.parent_span_id == trace.root_span_id
    assert second_span.parent_span_id == trace.root_span_id


@pytest.mark.asyncio
async def test_store_rejects_mismatched_trace_identity():
    store = InMemoryTelemetryStore()
    await store.upsert_trace(
        TelemetryTrace(trace_id="trace-a", root_span_id="span-root")
    )
    event = TelemetryEvent(
        trace_id="trace-b",
        event_type="agent_start",
        actor=TelemetryActor(type=ActorType.AGENT),
    )

    with pytest.raises(ValueError, match="trace_id"):
        await store.append_event("trace-a", event)


@pytest.mark.asyncio
async def test_upsert_trace_with_events_is_visible_to_stream():
    store = InMemoryTelemetryStore()
    trace = TelemetryTrace(
        trace_id="trace-upsert",
        root_span_id="span-root",
        events=[
            TelemetryEvent(
                trace_id="trace-upsert",
                event_type="agent_start",
                actor=TelemetryActor(type=ActorType.AGENT),
                sequence_number=1,
            )
        ],
    )

    await store.upsert_trace(trace)
    events = await store.get_events_after(TelemetryStreamScope(trace_id="trace-upsert"), None)

    assert [event.event_type for event in events] == ["agent_start"]


@pytest.mark.asyncio
async def test_upsert_trace_with_events_notifies_live_stream():
    store = InMemoryTelemetryStore()
    scope = TelemetryStreamScope(trace_id="trace-live-upsert")
    live = store.stream_after(scope, await store.get_stream_cursor(scope))
    next_event = asyncio.create_task(anext(live))

    await store.upsert_trace(
        TelemetryTrace(
            trace_id="trace-live-upsert",
            root_span_id="span-root",
            events=[
                TelemetryEvent(
                    trace_id="trace-live-upsert",
                    event_type="agent_start",
                    actor=TelemetryActor(type=ActorType.AGENT),
                    sequence_number=1,
                )
            ],
        )
    )
    event = await asyncio.wait_for(next_event, timeout=1)
    await live.aclose()

    assert event.event_type == "agent_start"


@pytest.mark.asyncio
async def test_upsert_trace_rejects_embedded_trace_id_mismatch():
    store = InMemoryTelemetryStore()

    with pytest.raises(ValueError, match="trace_id"):
        await store.upsert_trace(
            TelemetryTrace(
                trace_id="trace-parent",
                root_span_id="span-root",
                events=[
                    TelemetryEvent(
                        trace_id="trace-child",
                        event_type="agent_start",
                        actor=TelemetryActor(type=ActorType.AGENT),
                    )
                ],
            )
        )


@pytest.mark.asyncio
async def test_stale_upsert_does_not_regress_completed_trace_or_span():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-no-regress")
    await recorder.end_trace()
    completed = await store.get_trace("trace-no-regress")

    await store.upsert_trace(
        TelemetryTrace(
            trace_id="trace-no-regress",
            root_span_id=completed.root_span_id,
            status=TraceStatus.RUNNING,
            spans=[
                TelemetrySpan(
                    trace_id="trace-no-regress",
                    span_id=completed.root_span_id,
                    name="agent.run",
                    kind="agent.run",
                    actor=TelemetryActor(type=ActorType.AGENT),
                    status=SpanStatus.RUNNING,
                )
            ],
        )
    )
    trace = await store.get_trace("trace-no-regress")
    root_span = next(span for span in trace.spans if span.span_id == trace.root_span_id)

    assert trace.status == TraceStatus.COMPLETED
    assert root_span.status == SpanStatus.OK


@pytest.mark.asyncio
async def test_best_effort_end_trace_suppresses_read_errors():
    class BrokenReadStore(InMemoryTelemetryStore):
        async def get_trace(self, trace_id):
            raise RuntimeError("read failed")

    recorder = TelemetryRecorder(BrokenReadStore(), TelemetryConfig(strict=False))
    await recorder.start_trace(trace_id="trace-read")

    await recorder.end_trace()


@pytest.mark.asyncio
async def test_strict_end_trace_read_failure_restores_parent_context():
    class BrokenReadStore(InMemoryTelemetryStore):
        async def get_trace(self, trace_id):
            raise RuntimeError("read failed")

    outer_store = InMemoryTelemetryStore()
    outer = TelemetryRecorder(outer_store)
    await outer.start_trace(trace_id="trace-outer")
    parent_context = current_telemetry_context()
    inner = TelemetryRecorder(BrokenReadStore(), TelemetryConfig(strict=True))
    await inner.start_trace(trace_id="trace-inner")

    with pytest.raises(RuntimeError, match="read failed"):
        await inner.end_trace()

    assert current_telemetry_context() == parent_context


@pytest.mark.asyncio
async def test_nested_trace_end_restores_outer_context():
    outer = TelemetryRecorder(InMemoryTelemetryStore())
    await outer.start_trace(trace_id="trace-outer")
    parent_context = current_telemetry_context()
    inner = TelemetryRecorder(InMemoryTelemetryStore())
    await inner.start_trace(trace_id="trace-inner")
    await inner.end_trace()

    assert current_telemetry_context() == parent_context


@pytest.mark.asyncio
async def test_nested_trace_records_parent_trace_and_span_link():
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    await recorder.start_trace(trace_id="trace-parent")
    parent_context = current_telemetry_context()

    await recorder.start_trace(trace_id="trace-child")

    child = await recorder.store.get_trace("trace-child")
    assert child.parent_trace_id == "trace-parent"
    assert child.parent_span_id == parent_context.span_id

    await recorder.end_trace()
    await recorder.end_trace()


@pytest.mark.asyncio
async def test_end_trace_closes_active_child_spans_and_clears_context():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-child-end")
    await recorder.start_span(name="child", kind="tool.call")

    await recorder.end_trace()

    trace = await store.get_trace("trace-child-end")
    child = next(span for span in trace.spans if span.name == "child")
    root = next(span for span in trace.spans if span.span_id == trace.root_span_id)

    assert trace.status == TraceStatus.COMPLETED
    assert root.status == SpanStatus.OK
    assert child.status == SpanStatus.ERROR
    assert child.ended_at is not None
    assert current_telemetry_context() is None


@pytest.mark.asyncio
async def test_privacy_flags_block_model_and_tool_payloads():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(
        store,
        TelemetryConfig(capture="default", record_tool_results=False),
    )
    await recorder.start_trace(trace_id="trace-privacy")
    await recorder.emit_event("model_call", input={"prompt": "secret prompt"})
    await recorder.emit_event("model_response", output={"content": "secret response"})
    await recorder.emit_event("tool_result", output={"data": "tool result"})
    await recorder.emit_event("mcp_tool_result", output={"data": "mcp result"})
    await recorder.emit_event("tool_observation", output={"data": "observation"})

    trace = await store.get_trace("trace-privacy")

    assert trace.events[0].input is None
    assert trace.events[1].output is None
    assert trace.events[2].output is None
    assert trace.events[3].output is None
    assert trace.events[4].output is None
    assert trace.events[1].output_capture.state == CaptureState.NOT_RECORDED
    assert trace.events[2].output_capture.state == CaptureState.NOT_RECORDED
    assert trace.events[4].output_capture.state == CaptureState.NOT_RECORDED


def test_redaction_covers_common_secret_key_variants():
    event = TelemetryEvent(
        trace_id="trace-secret",
        event_type="agent_start",
        actor=TelemetryActor(type=ActorType.AGENT),
        input={
            "access_token": "a",
            "apiKey": "b",
            "client_secret": "c",
            "set-cookie": "d",
        },
    )
    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    redacted = recorder._record_input(event.input)

    assert set(redacted.values()) == {"[REDACTED]"}


def test_root_and_core_exports_include_telemetry_companions():
    import omnicoreagent
    import omnicoreagent.core as core

    for module in (omnicoreagent, core):
        assert module.TelemetryActor is TelemetryActor
        assert module.ActorType is ActorType
        assert module.TraceStatus is TraceStatus
        assert module.SpanStatus is SpanStatus
        assert module.TraceFilter is TraceFilter
        assert module.TelemetryTraceMetadata is TelemetryTraceMetadata
        assert module.OTelTraceMapper is OTelTraceMapper
        assert module.InMemoryTelemetryExporter is InMemoryTelemetryExporter


@pytest.mark.asyncio
async def test_end_trace_counts_final_output_capture_gap():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(record_outputs=False))
    context = await recorder.start_trace(trace_id="trace-final-gap")

    await recorder.end_trace(output={"response": "private answer"})

    trace = await store.get_trace(context.trace_id)
    root = next(span for span in trace.spans if span.span_id == trace.root_span_id)
    assert root.output_capture.state == CaptureState.NOT_RECORDED
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL


@pytest.mark.asyncio
async def test_end_trace_read_failure_persists_partial_closed_trace():
    class BrokenReadStore(InMemoryTelemetryStore):
        async def get_trace(self, trace_id):
            raise RuntimeError("read failed")

    store = BrokenReadStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(strict=False))
    await recorder.start_trace(trace_id="trace-read-fallback")

    await recorder.end_trace()

    trace = await InMemoryTelemetryStore.get_trace(store, "trace-read-fallback")
    root = next(span for span in trace.spans if span.span_id == trace.root_span_id)
    assert trace.incomplete is True
    assert trace.status == TraceStatus.COMPLETED
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL
    assert root.status != SpanStatus.RUNNING
    assert root.ended_at is not None


@pytest.mark.asyncio
async def test_recorder_redacts_error_message_and_stack():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(trace_id="trace-error-redaction")

    await recorder.emit_event(
        "runtime_error",
        error={
            "type": "ProviderError",
            "message": "failed for a@b.com api_key=sk-SECRET Authorization: Bearer abc.def",
            "stack": 'File "x.py"\n  token="tok-SECRET"',
        },
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    error = trace.events[-1].error
    assert "sk-SECRET" not in error.message
    assert "abc.def" not in error.message
    assert "a@b.com" not in error.message
    assert "ProviderError" == error.type
    assert "tok-SECRET" not in error.stack


@pytest.mark.asyncio
async def test_recorder_keeps_oversized_error_stack_reference():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(max_payload_bytes=200))
    context = await recorder.start_trace(trace_id="trace-large-stack")

    await recorder.emit_event(
        "runtime_error",
        error={"type": "Boom", "message": "boom", "stack": "frame\n" * 500},
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    stack = trace.events[-1].error.stack
    assert stack
    assert len(stack) < 3000
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL


@pytest.mark.asyncio
async def test_recorder_releases_per_trace_state_after_end():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(trace_id="trace-release")
    async with recorder.span(name="work", kind="agent.step"):
        pass

    await recorder.end_trace()

    assert recorder._trace_templates == {}
    assert recorder._incomplete_trace_ids == set()
    assert recorder._span_parent_contexts == {}
    assert recorder._span_sources == {}


@pytest.mark.asyncio
async def test_privacy_filter_failure_never_persists_raw_payload():
    class BrokenPrivacyFilter:
        def redact(self, value, *, boundary):
            raise RuntimeError("filter unavailable")

        def redact_text(self, value, *, boundary):
            raise RuntimeError("filter unavailable")

    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, privacy_filter=BrokenPrivacyFilter())
    context = await recorder.start_trace(trace_id="trace-privacy-failure")
    await recorder.emit_event(
        "user_message", input={"content": "reach me at a@b.com"}
    )
    await recorder.emit_event(
        "runtime_error",
        error={"type": "Boom", "message": "failed for a@b.com", "stack": "a@b.com"},
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    error = trace.events[-1].error
    assert error.message.startswith("[not recorded: privacy redaction failed")
    event = trace.events[-2]
    assert event.input is None
    assert event.input_capture.state == CaptureState.NOT_RECORDED
    assert "privacy redaction failed" in event.input_capture.reason
    assert "a@b.com" not in str(trace.model_dump())
    assert trace.incomplete is True
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL


@pytest.mark.asyncio
async def test_payload_failure_outside_trace_does_not_mark_next_trace():
    class BrokenPrivacyFilter:
        def redact(self, value, *, boundary):
            raise RuntimeError("filter unavailable")

    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, privacy_filter=BrokenPrivacyFilter())
    recorder._record_payload({"value": 1})
    recorder.privacy_filter = PrivacyFilter()

    context = await recorder.start_trace(trace_id="trace-after-orphan-failure")
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert trace.incomplete is False
    assert trace.evidence_status == TraceEvidenceStatus.COMPLETE


@pytest.mark.asyncio
async def test_offload_failure_falls_back_to_truncation_with_reason():
    class BrokenPayloadStore:
        def write(self, value, *, checksum, content_type):
            raise OSError("disk full")

    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(
        store,
        TelemetryConfig(max_payload_bytes=100, offload_large_payloads=True),
        payload_store=BrokenPayloadStore(),
    )
    context = await recorder.start_trace(trace_id="trace-offload-failure")
    await recorder.emit_event("tool_result", output={"content": "x" * 1000})
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    capture = trace.events[-1].output_capture
    assert capture.state == CaptureState.TRUNCATED
    assert "payload offload failed: OSError" in capture.reason
    assert trace.incomplete is True
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL


def test_redaction_keeps_token_usage_and_limits_visible():
    from omnicoreagent.core.telemetry.redaction import redact_sensitive_payload

    redacted = redact_sensitive_payload(
        {
            "usage": {
                "request_tokens": 10,
                "response_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 2},
                "prompt_token_count": 10,
            },
            "max_tokens": 100,
            "token_limit": 2000,
            "tokenizer": "cl100k",
            "time_to_first_token_ms": 42.5,
            "token_bytes": 12,
        },
        TelemetryConfig(),
    )

    assert redacted == {
        "usage": {
            "request_tokens": 10,
            "response_tokens": 5,
            "total_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 2},
            "prompt_token_count": 10,
        },
        "max_tokens": 100,
        "token_limit": 2000,
        "tokenizer": "cl100k",
        "time_to_first_token_ms": 42.5,
        "token_bytes": 12,
    }


def test_redaction_matches_secret_key_segments_and_spellings():
    from omnicoreagent.core.telemetry.redaction import redact_sensitive_payload

    secrets = {
        "token": "a",
        "accessToken": "b",
        "x-api-key": "c",
        "sessiontoken": "d",
        "id_token": "e",
        "db_password": "f",
        "Authorization": "g",
        "secret_key": "h",
        "Set-Cookie": "i",
    }

    redacted = redact_sensitive_payload(secrets, TelemetryConfig())

    assert set(redacted.values()) == {"[REDACTED]"}
