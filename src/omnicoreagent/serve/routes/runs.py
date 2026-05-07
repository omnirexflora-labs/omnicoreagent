"""Agent run routes for OmniServe."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from omnicoreagent.core.logging import logger

from ..models import ErrorResponse, RunRequest, RunResponse
from ..serialization import normalize_run_result
from ..sse import run_agent_stream
from ..state import get_agent, get_agent_name, get_config, resolve_session_id


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

        try:
            run_coro = agent.run(body.query, session_id=session_id)
            if config.request_timeout > 0:
                result = await asyncio.wait_for(
                    run_coro,
                    timeout=config.request_timeout,
                )
            else:
                result = await run_coro
            normalized = normalize_run_result(result, agent_name=get_agent_name(agent))
            return RunResponse(session_id=session_id, **normalized)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"Request timed out after {config.request_timeout} seconds",
            )
        except Exception as exc:
            logger.error(f"OmniServe: Run error - {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    return router
