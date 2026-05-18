"""Telemetry helpers for OmniServe request boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, Signature, signature
from typing import Any

from omnicoreagent.core.telemetry import (
    AbstractTelemetryStore,
    ActorType,
    TelemetryActor,
    TelemetryRecorder,
    TraceStatus,
)


@dataclass
class ServeTelemetryTrace:
    recorder: TelemetryRecorder
    trace_id: str


def accepts_keyword(run_signature: Signature, name: str) -> bool:
    return name in run_signature.parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD
        for parameter in run_signature.parameters.values()
    )


def build_run_kwargs(agent: Any, *, session_id: str, run_id: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"session_id": session_id}
    try:
        run_signature = signature(agent.run)
        if accepts_keyword(run_signature, "run_id"):
            kwargs["run_id"] = run_id
    except (TypeError, ValueError):
        kwargs["run_id"] = run_id
    return kwargs


async def start_serve_trace(
    agent: Any,
    *,
    method: str,
    path: str,
    session_id: str,
    run_id: str,
    query: str | None = None,
    streaming: bool = False,
) -> ServeTelemetryTrace | None:
    store = _telemetry_store(agent)
    if store is None:
        return None
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        name="serve.request",
        kind="serve.request",
        actor=TelemetryActor(type=ActorType.SERVE, name="OmniServe"),
        run_id=run_id,
        session_id=session_id,
        metadata={"tags": ["serve"]},
        input={
            "method": method,
            "path": path,
            "query_length": len(query or ""),
            "streaming": streaming,
        },
    )
    await recorder.emit_event(
        "serve_request_start",
        actor=TelemetryActor(type=ActorType.SERVE, name="OmniServe"),
        input={"method": method, "path": path, "streaming": streaming},
    )
    return ServeTelemetryTrace(recorder=recorder, trace_id=context.trace_id)


async def finish_serve_trace(
    trace: ServeTelemetryTrace | None,
    *,
    status: TraceStatus | str = TraceStatus.COMPLETED,
    output: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    if trace is None:
        return
    event_type = "serve_request_error" if error else "serve_request_end"
    await trace.recorder.emit_event(
        event_type,
        actor=TelemetryActor(type=ActorType.SERVE, name="OmniServe"),
        output=output,
        error=error,
    )
    await trace.recorder.end_trace(status=status, output=output, error=error)


def _telemetry_store(agent: Any) -> Any | None:
    ensure_telemetry = getattr(agent, "_ensure_telemetry", None)
    if callable(ensure_telemetry):
        ensure_telemetry()
    store = getattr(agent, "telemetry_store", None)
    if _is_telemetry_store(store):
        return store
    stream = getattr(agent, "telemetry_stream", None)
    stream_store = getattr(stream, "store", None)
    if _is_telemetry_store(stream_store):
        return stream_store
    fallback_store = getattr(agent, "store", None)
    if _is_telemetry_store(fallback_store):
        return fallback_store
    return None


def _is_telemetry_store(store: Any) -> bool:
    return isinstance(store, AbstractTelemetryStore)
