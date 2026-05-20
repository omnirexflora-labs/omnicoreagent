"""Telemetry inspection routes for OmniServe."""

from __future__ import annotations

from inspect import isawaitable, signature
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from omnicoreagent.core.telemetry import TraceStatus

from ..models import (
    TelemetryEventsResponse,
    TelemetryTraceDetailResponse,
    TelemetryTraceListResponse,
)
from ..serialization import normalize_events
from ..sse import stream_session_events
from ..state import get_agent


def _clean_filters(**filters: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in filters.items()
        if value is not None and value != () and value != []
    }


def _supported_kwargs(method: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = signature(method).parameters
    except (TypeError, ValueError):
        return kwargs

    if any(parameter.kind.name == "VAR_KEYWORD" for parameter in parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in parameters}


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


async def _call(method: Any, **kwargs: Any) -> Any:
    return await _maybe_await(method(**_supported_kwargs(method, kwargs)))


def _event_value(event: dict[str, Any], key: str) -> Any:
    value = event.get(key)
    if value is not None:
        return value
    metadata = event.get("metadata") or {}
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _event_matches_filters(
    event: dict[str, Any],
    *,
    trace_id: str | None,
    run_id: str | None,
    session_id: str | None,
    task_id: str | None,
    event_types: tuple[str, ...] | None,
) -> bool:
    checks = {
        "trace_id": trace_id,
        "run_id": run_id,
        "session_id": session_id,
        "task_id": task_id,
    }
    for key, expected in checks.items():
        if expected is not None and _event_value(event, key) != expected:
            return False
    if event_types and event.get("event_type") not in event_types:
        return False
    return True


def _trace_summary(trace: dict[str, Any] | None) -> dict[str, Any]:
    trace = trace or {}
    events = trace.get("events", [])
    spans = trace.get("spans", [])
    return {
        "trace_id": trace.get("trace_id"),
        "run_id": trace.get("run_id"),
        "session_id": trace.get("session_id"),
        "status": trace.get("status"),
        "event_count": len(events),
        "span_count": len(spans),
    }


def _validate_status(status: str | None) -> str | None:
    if status is None:
        return None
    try:
        return TraceStatus(status).value
    except ValueError:
        allowed = ", ".join(item.value for item in TraceStatus)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid trace status '{status}'. Expected one of: {allowed}",
        ) from None


def _require_trace(
    trace: dict[str, Any] | None,
    detail: str,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if trace is None:
        raise HTTPException(status_code=404, detail=detail)
    if trace_id is not None and trace.get("trace_id") != trace_id:
        raise HTTPException(status_code=404, detail=detail)
    if run_id is not None and trace.get("run_id") != run_id:
        raise HTTPException(status_code=404, detail=detail)
    if session_id is not None and trace.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail=detail)
    return trace


async def _get_events(
    agent: Any,
    *,
    cursor: str | None,
    trace_id: str | None,
    run_id: str | None,
    session_id: str | None,
    task_id: str | None,
    event_types: tuple[str, ...] | None,
    limit: int,
) -> list[dict[str, Any]]:
    events_after = getattr(agent, "get_telemetry_events_after", None)
    if not callable(events_after):
        return []

    raw_events = await _call(
        events_after,
        cursor=cursor,
        trace_id=trace_id,
        run_id=run_id,
        session_id=session_id,
        task_id=task_id,
        event_types=event_types,
    )
    events = normalize_events(raw_events or [])
    filtered = [
        event
        for event in events
        if _event_matches_filters(
            event,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            event_types=event_types,
        )
    ]
    return filtered[:limit]


async def _get_trace(
    agent: Any,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    normalize: bool = False,
) -> dict[str, Any] | None:
    if session_id is not None and trace_id is None and run_id is None:
        get_latest_trace = getattr(agent, "get_latest_trace", None)
        if callable(get_latest_trace):
            return await _call(get_latest_trace, session_id=session_id, normalize=normalize)

    get_trace = getattr(agent, "get_trace", None)
    if not callable(get_trace):
        return None

    trace = await _call(
        get_trace,
        **_clean_filters(trace_id=trace_id, run_id=run_id, session_id=session_id),
        normalize=normalize,
    )
    return trace or None


async def _list_traces(
    agent: Any,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    workflow_id: str | None = None,
    model: str | None = None,
    status: str | None = None,
    normalize: bool = False,
) -> list[dict[str, Any]]:
    list_traces = getattr(agent, "list_telemetry_traces", None)
    if not callable(list_traces):
        trace = await _get_trace(
            agent,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            normalize=normalize,
        )
        return [trace] if trace else []

    traces = await _call(
        list_traces,
        trace_id=trace_id,
        run_id=run_id,
        session_id=session_id,
        task_id=task_id,
        agent_id=agent_id,
        workflow_id=workflow_id,
        model=model,
        status=status,
        normalize=normalize,
    )
    return list(traces or [])


def create_telemetry_router() -> APIRouter:
    """Create telemetry inspection endpoints."""
    router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

    @router.get(
        "/events",
        response_model=TelemetryEventsResponse,
        summary="List telemetry events",
        description="Return stored telemetry events filtered by trace, run, session, task, or event type.",
    )
    async def list_events(
        request: Request,
        trace_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        event_type: list[str] | None = Query(default=None),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> TelemetryEventsResponse:
        event_types = tuple(event_type or ()) or None
        agent = get_agent(request)
        events = await _get_events(
            agent,
            cursor=cursor,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            event_types=event_types,
            limit=limit,
        )
        return TelemetryEventsResponse(
            filters=_clean_filters(
                cursor=cursor,
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                task_id=task_id,
                event_type=list(event_types) if event_types else None,
                limit=limit,
            ),
            events=events,
            count=len(events),
        )

    @router.get(
        "/events/stream",
        summary="Stream telemetry events",
        description="Replay and follow telemetry events for one session. Add run_id to isolate one run.",
    )
    async def stream_events(
        request: Request,
        session_id: str = Query(...),
        run_id: str | None = Query(default=None),
    ):
        agent = get_agent(request)
        return StreamingResponse(
            stream_session_events(agent, session_id, run_id=run_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get(
        "/traces",
        response_model=TelemetryTraceListResponse,
        summary="List telemetry traces",
        description="Return telemetry traces filtered by trace, run, session, task, agent, workflow, model, or status.",
    )
    async def list_traces(
        request: Request,
        trace_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        workflow_id: str | None = Query(default=None),
        model: str | None = Query(default=None),
        status: str | None = Query(default=None),
        normalize: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> TelemetryTraceListResponse:
        agent = get_agent(request)
        status = _validate_status(status)
        traces = await _list_traces(
            agent,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
            model=model,
            status=status,
            normalize=normalize,
        )
        traces = traces[:limit]
        return TelemetryTraceListResponse(
            filters=_clean_filters(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                task_id=task_id,
                agent_id=agent_id,
                workflow_id=workflow_id,
                model=model,
                status=status,
                normalize=normalize,
                limit=limit,
            ),
            traces=traces,
            count=len(traces),
        )

    @router.get(
        "/traces/{trace_id}",
        response_model=TelemetryTraceDetailResponse,
        summary="Get exact telemetry trace",
        description="Return one telemetry trace by exact trace_id.",
    )
    async def get_exact_trace(
        request: Request,
        trace_id: str,
        normalize: bool = Query(default=False),
    ) -> TelemetryTraceDetailResponse:
        agent = get_agent(request)
        trace = _require_trace(
            await _get_trace(agent, trace_id=trace_id, normalize=normalize),
            f"Trace not found: {trace_id}",
            trace_id=trace_id,
        )
        return TelemetryTraceDetailResponse(
            filters=_clean_filters(trace_id=trace_id, normalize=normalize),
            summary=_trace_summary(trace),
            trace=trace,
        )

    @router.get(
        "/runs/{run_id}/trace",
        response_model=TelemetryTraceDetailResponse,
        summary="Get run telemetry trace",
        description="Return the latest telemetry trace correlated to one run_id.",
    )
    async def get_run_trace(
        request: Request,
        run_id: str,
        normalize: bool = Query(default=False),
    ) -> TelemetryTraceDetailResponse:
        agent = get_agent(request)
        trace = _require_trace(
            await _get_trace(agent, run_id=run_id, normalize=normalize),
            f"Trace not found for run: {run_id}",
            run_id=run_id,
        )
        return TelemetryTraceDetailResponse(
            filters=_clean_filters(run_id=run_id, normalize=normalize),
            summary=_trace_summary(trace),
            trace=trace,
        )

    @router.get(
        "/sessions/{session_id}/trace",
        response_model=TelemetryTraceDetailResponse,
        summary="Get latest session telemetry trace",
        description="Return the latest telemetry trace for one session_id.",
    )
    async def get_session_trace(
        request: Request,
        session_id: str,
        normalize: bool = Query(default=False),
    ) -> TelemetryTraceDetailResponse:
        agent = get_agent(request)
        trace = _require_trace(
            await _get_trace(agent, session_id=session_id, normalize=normalize),
            f"Trace not found for session: {session_id}",
            session_id=session_id,
        )
        return TelemetryTraceDetailResponse(
            filters=_clean_filters(session_id=session_id, normalize=normalize),
            summary=_trace_summary(trace),
            trace=trace,
        )

    return router
