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
        summary="Stream session events (SSE)",
        description="Stream events for a specific session via SSE.",
    )
    async def stream_events(request: Request, session_id: str):
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
        summary="Get session events",
        description="Get all events for a specific session as JSON.",
    )
    async def get_events(request: Request, session_id: str) -> EventsResponse:
        agent = get_agent(request)
        events = normalize_events(await agent.get_events(session_id))

        return EventsResponse(
            session_id=session_id,
            events=events,
            count=len(events),
        )

    @router.get(
        "/events/{session_id}/trace",
        response_model=TraceResponse,
        summary="Get session event summary",
        description="Build a compact summary from stored session events.",
    )
    async def get_trace(request: Request, session_id: str) -> TraceResponse:
        agent = get_agent(request)
        trace = await agent.get_trace(session_id)

        return TraceResponse(
            session_id=session_id,
            summary=trace.get("summary", {}),
            steps=trace.get("steps", []),
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
