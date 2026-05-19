"""Session and event routes for OmniServe."""

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..models import EventsResponse, SessionHistoryResponse, TraceResponse
from ..serialization import normalize_events
from ..sse import stream_session_events
from ..state import get_agent


async def _get_telemetry_events(
    agent: Any,
    *,
    session_id: str,
    run_id: str | None,
) -> list[Any]:
    events_after = getattr(agent, "get_telemetry_events_after", None)
    if not callable(events_after):
        return []

    result = events_after(cursor=None, session_id=session_id, run_id=run_id)
    if isawaitable(result):
        return await result
    return list(result or [])


async def _maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def _event_matches_requested_run(event: dict[str, Any], run_id: str | None) -> bool:
    if run_id is None:
        return True
    metadata = event.get("metadata") or {}
    event_run_id = event.get("run_id")
    if event_run_id is None and isinstance(metadata, dict):
        event_run_id = metadata.get("run_id")
    return event_run_id == run_id


async def _get_session_trace(
    agent: Any,
    *,
    session_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    if run_id is not None:
        get_trace = getattr(agent, "get_trace", None)
        if not callable(get_trace):
            return {}
        result = get_trace(run_id=run_id)
    else:
        get_latest_trace = getattr(agent, "get_latest_trace", None)
        if not callable(get_latest_trace):
            return {}
        result = get_latest_trace(session_id)

    if isawaitable(result):
        result = await result
    return result or {}


def create_sessions_router() -> APIRouter:
    """Create session history and event endpoints."""
    router = APIRouter(tags=["Sessions"])

    @router.get(
        "/events/{session_id}",
        summary="Stream telemetry events (SSE)",
        description="Replay stored telemetry events for a session, then follow live telemetry events over SSE.",
    )
    async def stream_telemetry_events(
        request: Request,
        session_id: str,
        run_id: str | None = Query(
            default=None,
            description="Optional run id to isolate one run inside the session.",
        ),
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
        "/events/{session_id}/list",
        response_model=EventsResponse,
        summary="Get telemetry events",
        description="Get stored telemetry events for a specific session as JSON.",
    )
    async def list_telemetry_events(
        request: Request,
        session_id: str,
        run_id: str | None = Query(
            default=None,
            description="Optional run id to isolate one run inside the session.",
        ),
    ) -> EventsResponse:
        agent = get_agent(request)
        raw_events = await _get_telemetry_events(
            agent,
            session_id=session_id,
            run_id=run_id,
        )
        events = [
            event
            for event in normalize_events(raw_events)
            if _event_matches_requested_run(event, run_id)
        ]

        return EventsResponse(
            session_id=session_id,
            events=events,
            count=len(events),
        )

    @router.get(
        "/events/{session_id}/trace",
        response_model=TraceResponse,
        summary="Get telemetry trace summary",
        description="Build a compact summary from the latest stored telemetry trace for the session.",
    )
    async def get_trace(
        request: Request,
        session_id: str,
        run_id: str | None = Query(
            default=None,
            description="Optional run id. When omitted, the latest session trace is returned.",
        ),
    ) -> TraceResponse:
        agent = get_agent(request)
        trace = await _get_session_trace(agent, session_id=session_id, run_id=run_id)
        if run_id is not None and trace.get("session_id") != session_id:
            trace = {}
        elif run_id is None and trace.get("session_id") not in {None, session_id}:
            trace = {}
        events = trace.get("events", [])
        spans = trace.get("spans", [])

        return TraceResponse(
            session_id=session_id,
            summary={
                "trace_id": trace.get("trace_id"),
                "run_id": trace.get("run_id"),
                "status": trace.get("status"),
                "event_count": len(events),
                "span_count": len(spans),
            },
            steps=events,
        )

    @router.get(
        "/sessions/{session_id}/history",
        response_model=SessionHistoryResponse,
        summary="Get session history",
        description="Get message history for a specific session.",
    )
    async def get_session_history(
        request: Request, session_id: str
    ) -> SessionHistoryResponse:
        agent = get_agent(request)
        history_method = getattr(agent, "get_session_history", None)
        messages = []
        if callable(history_method):
            messages = await _maybe_await(history_method(session_id))
            messages = messages or []

        return SessionHistoryResponse(
            session_id=session_id,
            messages=messages,
            count=len(messages),
        )

    @router.delete(
        "/sessions/{session_id}",
        summary="Clear session history",
        description="Clear message history for a specific session.",
    )
    async def clear_session(request: Request, session_id: str):
        agent = get_agent(request)
        clear_method = getattr(agent, "clear_session_history", None)
        if callable(clear_method):
            await _maybe_await(clear_method(session_id))
        return {"status": "cleared", "session_id": session_id}

    return router
