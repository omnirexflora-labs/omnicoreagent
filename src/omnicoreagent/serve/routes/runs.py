"""Agent run routes for OmniServe."""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from omnicoreagent.core.logging import logger
from omnicoreagent.core.telemetry import TraceStatus

from ..models import ErrorResponse, RunRequest, RunResponse
from ..serialization import normalize_run_result
from ..sse import run_agent_stream
from ..state import get_agent, get_agent_name, get_config, resolve_session_id
from ..telemetry import build_run_kwargs, finish_serve_trace, start_serve_trace


def create_runs_router() -> APIRouter:
    """Create agent run endpoints."""
    router = APIRouter(tags=["Runs"])

    @router.post(
        "/run",
        summary="Run agent (SSE streaming)",
        description="Run the agent with a query and stream SSE events.",
        responses={
            200: {"description": "SSE stream of agent events"},
            500: {"model": ErrorResponse},
        },
    )
    async def run_agent_sse(request: Request, body: RunRequest):
        agent = get_agent(request)
        config = get_config(request)
        session_id = resolve_session_id(agent, body.session_id)

        logger.info(
            f"OmniServe: SSE run request - session={session_id}, "
            f"query_length={len(body.query)}"
        )

        return StreamingResponse(
            run_agent_stream(
                agent,
                body.query,
                session_id,
                timeout_seconds=config.request_timeout,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post(
        "/run/sync",
        response_model=RunResponse,
        summary="Run agent (synchronous)",
        description="Run the agent with a query and return a JSON response.",
        responses={500: {"model": ErrorResponse}},
    )
    async def run_agent_sync(request: Request, body: RunRequest) -> RunResponse:
        agent = get_agent(request)
        config = get_config(request)
        session_id = resolve_session_id(agent, body.session_id)

        logger.info(
            f"OmniServe: Sync run request - session={session_id}, "
            f"query_length={len(body.query)}"
        )

        serve_trace = None
        try:
            run_id = f"run_{uuid4().hex}"
            serve_trace = await start_serve_trace(
                agent,
                method="POST",
                path="/run/sync",
                session_id=session_id,
                run_id=run_id,
                query=body.query,
                streaming=False,
            )
            run_coro = agent.run(
                body.query,
                **build_run_kwargs(agent, session_id=session_id, run_id=run_id),
            )
            if config.request_timeout > 0:
                result = await asyncio.wait_for(
                    run_coro,
                    timeout=config.request_timeout,
                )
            else:
                result = await run_coro
            normalized = normalize_run_result(result, agent_name=get_agent_name(agent))
            normalized["run_id"] = normalized.get("run_id") or run_id
            await finish_serve_trace(
                serve_trace,
                output={
                    "status": "completed",
                    "agent_trace_id": normalized.get("trace_id"),
                },
            )
            return RunResponse(session_id=session_id, **normalized)
        except asyncio.TimeoutError:
            await finish_serve_trace(
                serve_trace,
                status=TraceStatus.TIMEOUT,
                error={"type": "TimeoutError", "message": "Request timed out"},
            )
            raise HTTPException(
                status_code=504,
                detail=f"Request timed out after {config.request_timeout} seconds",
            )
        except Exception as exc:
            logger.error(f"OmniServe: Run error - {exc}")
            await finish_serve_trace(
                serve_trace,
                status=TraceStatus.FAILED,
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise HTTPException(status_code=500, detail=str(exc))

    return router
