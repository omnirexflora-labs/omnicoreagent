"""Agent run routes for OmniServe."""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from omnicoreagent.core.logging import logger
from omnicoreagent.core.runtime.deadline import (
    complete_despite_cancellation,
    run_with_timeout,
)
from omnicoreagent.core.telemetry import TraceStatus

from ..models import (
    ApprovalDecisionRequest,
    BudgetDecisionRequest,
    OutcomeRequest,
    ErrorResponse,
    RunRequest,
    RunResponse,
    SteerRequest,
)
from ..serialization import normalize_run_result
from ..sse import run_agent_stream
from ..state import get_agent, get_agent_name, get_config, resolve_session_id
from ..telemetry import build_run_kwargs, finish_serve_trace, start_serve_trace


def serve_trace_status(run_status: str) -> TraceStatus:
    """The request trace's status for an agent run outcome."""
    if run_status == "success":
        return TraceStatus.COMPLETED
    if run_status in {"awaiting_approval", "awaiting_budget"}:
        return TraceStatus.SUSPENDED
    return TraceStatus.FAILED


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
            # A deadline marks the agent trace as timed out, not cancelled.
            result = await run_with_timeout(run_coro, config.request_timeout)
            normalized = normalize_run_result(
                result,
                agent_name=get_agent_name(agent),
                privacy_filter=getattr(agent, "privacy_filter", None),
            )
            normalized["run_id"] = normalized.get("run_id") or run_id
            # The request trace reports the agent's real outcome.
            await finish_serve_trace(
                serve_trace,
                status=serve_trace_status(normalized.get("status", "success")),
                output={
                    "status": normalized.get("status", "success"),
                    "agent_trace_id": normalized.get("trace_id"),
                },
            )
            # Finished: an error below must not try to finish it again.
            serve_trace = None
            return RunResponse(session_id=session_id, **normalized)
        except asyncio.CancelledError:
            # A dropped or cancelled request must not leave its trace running;
            # the server may cancel again while this is recorded.
            await complete_despite_cancellation(
                finish_serve_trace(
                    serve_trace,
                    status=TraceStatus.CANCELLED,
                    error={"type": "CancelledError", "message": "Request cancelled"},
                )
            )
            raise
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
            message = str(exc)
            privacy_filter = getattr(agent, "privacy_filter", None)
            if privacy_filter is not None:
                message = privacy_filter.redact_text(message, boundary="public")
            raise HTTPException(status_code=500, detail=message)

    @router.get(
        "/runs/{run_id}",
        summary="Get a run",
        description=(
            "A run's durable record: status, step, usage, trace IDs, tool call "
            "states, and approvals. Its saved conversation is not returned."
        ),
        responses={404: {"model": ErrorResponse}},
    )
    async def get_run(request: Request, run_id: str) -> dict:
        agent = get_agent(request)
        record = await agent.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No run {run_id}")
        return _public_run(agent, record)

    @router.get(
        "/runs/{run_id}/trajectory",
        summary="Get a run's whole trajectory",
        description=(
            "The whole run as one story: every trace segment in order (a run "
            "that paused and resumed has one per stretch), totals summed across "
            "them, each tool call once with its final outcome, and the approvals. "
            "`/telemetry/runs/{run_id}/trajectory` is the latest segment only."
        ),
        responses={404: {"model": ErrorResponse}},
    )
    async def get_run_trajectory(request: Request, run_id: str) -> dict:
        agent = get_agent(request)
        story = await agent.get_run_trajectory(run_id)
        if story is None:
            raise HTTPException(status_code=404, detail=f"No run {run_id}")
        privacy_filter = getattr(agent, "privacy_filter", None)
        return privacy_filter.redact(story, boundary="public") if privacy_filter else story

    @router.post(
        "/runs/{run_id}/approvals/{approval_id}",
        summary="Decide an approval",
        description="Approve or deny an approval a paused run is waiting for.",
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def decide_approval(
        request: Request, run_id: str, approval_id: str, body: ApprovalDecisionRequest
    ) -> dict:
        agent = get_agent(request)
        try:
            approval = await agent.resolve_approval(
                run_id,
                approval_id,
                decision=body.decision,
                approver=body.approver,
                note=body.note,
                arguments=body.arguments,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return _public_view(agent, approval)

    @router.get(
        "/runs/{run_id}/budget",
        summary="Read a run's budgets",
        description=(
            "Every budget covering the run — its own, its session's, its agent's, "
            "the application's — with the limit, what is spent and reserved, and "
            "the ledger key. Empty when nothing is budgeted."
        ),
        responses={404: {"model": ErrorResponse}},
    )
    async def read_budgets(request: Request, run_id: str) -> dict:
        agent = get_agent(request)
        try:
            entries = await agent.budget_status(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return {"run_id": run_id, "budgets": entries}

    @router.post(
        "/runs/{run_id}/budget",
        summary="Decide a budget",
        description=(
            "Grant or refuse the budget a waiting run ran out of. A grant is a "
            "recorded exception to that one budget, with the name of whoever "
            "made it; the policy is unchanged. Resume the run afterwards."
        ),
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def decide_budget(
        request: Request, run_id: str, body: BudgetDecisionRequest
    ) -> dict:
        agent = get_agent(request)
        try:
            if body.decision == "grant":
                decided = await agent.grant_budget(
                    run_id, amount=body.amount, approver=body.approver, note=body.note
                )
            else:
                decided = await agent.deny_budget(
                    run_id, approver=body.approver, note=body.note
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return decided

    @router.post(
        "/runs/{run_id}/outcome",
        summary="Record a run's outcome",
        description=(
            "What the run turned out to be worth, reported whenever it is known: "
            "a pull request merged, an answer accepted, a test suite green. Kept "
            "on the run's record and in its trace; a run may gather several."
        ),
        responses={404: {"model": ErrorResponse}},
    )
    async def record_outcome(request: Request, run_id: str, body: OutcomeRequest) -> dict:
        agent = get_agent(request)
        try:
            return await agent.record_outcome(
                run_id,
                source=body.source,
                reward=body.reward,
                label=body.label,
                detail=body.detail,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.post(
        "/runs/{run_id}/steer",
        summary="Steer a run",
        description=(
            "Send a message to a running, waiting, or interrupted run; it arrives "
            "as a user message at the run's next step boundary. The injection "
            "guardrail checks it first (422 when blocked)."
        ),
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def steer_run(request: Request, run_id: str, body: SteerRequest) -> dict:
        agent = get_agent(request)
        try:
            result = await agent.steer(run_id, body.message, sender=body.sender)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if result.get("status") == "blocked":
            raise HTTPException(status_code=422, detail="Blocked by the injection guardrail")
        return result

    @router.post(
        "/runs/{run_id}/interrupt",
        summary="Interrupt a run",
        description="Stop a running run at its next step boundary; resume continues it.",
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def interrupt_run(request: Request, run_id: str) -> dict:
        agent = get_agent(request)
        try:
            return await agent.interrupt(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @router.post(
        "/runs/{run_id}/resume",
        response_model=RunResponse,
        summary="Resume a paused run",
        description="Continue a run once every approval it asked for is decided.",
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def resume_run(request: Request, run_id: str) -> RunResponse:
        agent = get_agent(request)
        config = get_config(request)
        try:
            result = await run_with_timeout(agent.resume(run_id), config.request_timeout)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"Request timed out after {config.request_timeout} seconds",
            ) from None
        normalized = normalize_run_result(
            result,
            agent_name=get_agent_name(agent),
            privacy_filter=getattr(agent, "privacy_filter", None),
        )
        return RunResponse(session_id=result.get("session_id"), **normalized)

    return router


_PUBLIC_APPROVAL_KEYS = (
    "approval_id", "status", "tool_call_id", "tool_name", "capability", "target",
    "risk_level", "reason", "created_at", "expires_at", "approver", "note",
    "decided_at", "edited_arguments", "delegated_run_id", "delegated_name",
)


def _public_view(agent, approval: dict, record: dict | None = None) -> dict:
    """An approval as a person needs it: its state and, given the run's
    record, the call as the model made it (the arguments come from the saved
    conversation, which is itself never returned)."""
    from omnicoreagent.core.runtime.omnicore_agent import _public_approval

    view = {key: approval.get(key) for key in _PUBLIC_APPROVAL_KEYS}
    if record is not None:
        view["arguments"] = _public_approval(approval, record).get("arguments")
    privacy_filter = getattr(agent, "privacy_filter", None)
    return privacy_filter.redact(view, boundary="public") if privacy_filter else view


def _public_run(agent, record: dict) -> dict:
    """A run without its saved conversation, behind the public privacy boundary."""
    view = {
        key: record.get(key)
        for key in (
            "run_id", "session_id", "agent_name", "agent_version", "status", "step",
            "trace_ids", "tool_calls", "usage", "error", "created_at", "updated_at",
        )
    }
    view["approvals"] = [_public_view(agent, a, record) for a in record.get("approvals") or []]
    view["budget_requests"] = list(record.get("budget_requests") or [])
    view["outcomes"] = list(record.get("outcomes") or [])
    privacy_filter = getattr(agent, "privacy_filter", None)
    return privacy_filter.redact(view, boundary="public") if privacy_filter else view
