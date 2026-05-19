"""Session and event routes for OmniServe."""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..models import EventsResponse, SessionHistoryResponse, TraceResponse
from ..serialization import normalize_events
from ..sse import stream_session_events
from ..state import get_agent


def create_sessions_router() -> APIRouter:
    """Create session history and event endpoints."""
    router = APIRouter(tags=["Sessions"])

    @router.get(
        "/events/{session_id}",
        summary="Stream telemetry events (SSE)",
        description="Replay stored telemetry events for a session, then follow live telemetry events over SSE.",
    )
    async def stream_telemetry_events(request: Request, session_id: str):
        agent = get_agent(request)
        return StreamingResponse(
            stream_session_events(agent, session_id),
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
    async def list_telemetry_events(request: Request, session_id: str) -> EventsResponse:
        agent = get_agent(request)
        events = normalize_events(
            await agent.get_telemetry_events_after(cursor=None, session_id=session_id)
        )

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
    async def get_trace(request: Request, session_id: str) -> TraceResponse:
        agent = get_agent(request)
        trace = await agent.get_latest_trace(session_id)
        trace = trace or {}
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
        messages = await agent.get_session_history(session_id)

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
        await agent.clear_session_history(session_id)
        return {"status": "cleared", "session_id": session_id}

    return router
