import asyncio

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    JsonlTelemetryStore,
    SpanStatus,
    TelemetryActor,
    TelemetryConfig,
    TelemetryEvent,
    TelemetryNormalizer,
    TelemetryRecorder,
    TelemetrySpan,
    TelemetryStream,
    TelemetryStreamScope,
    TelemetryTrace,
    TelemetryTraceMetadata,
    TraceFilter,
    TraceStatus,
    current_telemetry_context,
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
    assert trace.spans[-1].status == SpanStatus.OK
    assert current_telemetry_context() is None


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
        TelemetryConfig(record_tool_results=False),
    )
    await recorder.start_trace(trace_id="trace-privacy")
    await recorder.emit_event("model_call", input={"prompt": "secret prompt"})
    await recorder.emit_event("model_response", output={"content": "secret response"})
    await recorder.emit_event("tool_result", output={"data": "tool result"})
    await recorder.emit_event("mcp_tool_result", output={"data": "mcp result"})

    trace = await store.get_trace("trace-privacy")

    assert trace.events[0].input is None
    assert trace.events[1].output is None
    assert trace.events[2].output is None
    assert trace.events[3].output is None


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
