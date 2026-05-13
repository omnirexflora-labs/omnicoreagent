"""Durable background execution routes for OmniServe."""

from fastapi import APIRouter, HTTPException, Query, Request

from omnicoreagent.background import (
    AgentAlreadyRegisteredError,
    BackgroundAgentError,
    BackgroundAgentSpec,
    BackgroundAttempt,
    BackgroundRun,
    BackgroundTaskSpec,
    RunNotFoundError,
    TaskAlreadyRegisteredError,
    TaskNotFoundError,
)
from omnicoreagent.background.models import TERMINAL_RUN_STATUSES

from ..models import (
    BackgroundAgentRegistrationRequest,
    BackgroundAgentsResponse,
    BackgroundRunEventsResponse,
    BackgroundRunTimeoutResponse,
    BackgroundRunWorkspaceResponse,
    BackgroundRunsResponse,
    BackgroundStatusResponse,
    BackgroundTaskCreateRequest,
    BackgroundTaskPatchRequest,
    BackgroundTaskRunRequest,
    BackgroundTasksResponse,
    HttpErrorResponse,
)
from ..state import get_agent, get_background_manager, get_config


_HTTP_ERROR = {"model": HttpErrorResponse}


def create_background_router() -> APIRouter:
    """Create durable background execution endpoints."""
    router = APIRouter(prefix="/background", tags=["Background"])

    @router.post(
        "/agents",
        response_model=BackgroundAgentSpec,
        summary="Register a background agent",
        responses={409: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def register_background_agent(
        request: Request, body: BackgroundAgentRegistrationRequest
    ) -> BackgroundAgentSpec:
        manager = _require_background_manager(request)
        config = get_config(request)

        try:
            if body.spec is not None:
                spec = body.spec
                if body.agent_id and body.agent_id != spec.agent_id:
                    raise HTTPException(
                        status_code=422,
                        detail="agent_id must match spec.agent_id when spec is provided",
                    )
                return await manager.register_agent_spec(spec, replace=body.replace)
            return await manager.register_agent(
                body.agent_id or config.background_agent_id,
                get_agent(request),
                replace=body.replace,
            )
        except AgentAlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get(
        "/agents",
        response_model=BackgroundAgentsResponse,
        summary="List background agents",
    )
    async def list_background_agents(request: Request) -> BackgroundAgentsResponse:
        manager = _require_background_manager(request)
        agents = await manager.list_agents()
        return BackgroundAgentsResponse(agents=agents, total=len(agents))

    @router.get(
        "/agents/{agent_id}",
        response_model=BackgroundAgentSpec,
        summary="Get a background agent spec",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def get_background_agent(request: Request, agent_id: str) -> BackgroundAgentSpec:
        manager = _require_background_manager(request)
        agent = await manager.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        return agent

    @router.delete(
        "/agents/{agent_id}",
        response_model=BackgroundStatusResponse,
        summary="Delete a background agent spec",
        responses={
            404: _HTTP_ERROR,
            409: _HTTP_ERROR,
            503: _HTTP_ERROR,
        },
    )
    async def delete_background_agent(
        request: Request,
        agent_id: str,
        force: bool = Query(default=False),
    ) -> BackgroundStatusResponse:
        manager = _require_background_manager(request)
        if not await manager.get_agent(agent_id):
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
        try:
            await manager.unregister_agent(agent_id, force=force)
        except AgentAlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return BackgroundStatusResponse(status="deleted")

    @router.post(
        "/tasks",
        response_model=BackgroundTaskSpec,
        summary="Create a background task",
        responses={
            404: _HTTP_ERROR,
            409: _HTTP_ERROR,
            503: _HTTP_ERROR,
        },
    )
    async def create_background_task(
        request: Request, body: BackgroundTaskCreateRequest
    ) -> BackgroundTaskSpec:
        manager = _require_background_manager(request)
        config = get_config(request)
        try:
            return await manager.register_task(
                task_id=body.task_id,
                agent_id=body.agent_id or config.background_agent_id,
                query=body.query,
                schedule=body.schedule,
                enabled=body.enabled,
                timeout_seconds=body.timeout_seconds,
                retry_policy=body.retry_policy.model_dump(mode="python"),
                overlap_policy=body.overlap_policy,
                session_policy=body.session_policy.model_dump(mode="python"),
                workspace_policy=body.workspace_policy.model_dump(mode="python"),
                metadata=body.metadata,
                replace=body.replace,
            )
        except TaskAlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BackgroundAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/tasks",
        response_model=BackgroundTasksResponse,
        summary="List background tasks",
    )
    async def list_background_tasks(
        request: Request,
        agent_id: str | None = Query(default=None),
    ) -> BackgroundTasksResponse:
        manager = _require_background_manager(request)
        tasks = await manager.list_tasks(agent_id=agent_id)
        return BackgroundTasksResponse(tasks=tasks, total=len(tasks))

    @router.get(
        "/tasks/{task_id}",
        response_model=BackgroundTaskSpec,
        summary="Get a background task",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def get_background_task(request: Request, task_id: str) -> BackgroundTaskSpec:
        manager = _require_background_manager(request)
        task = await manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return task

    @router.patch(
        "/tasks/{task_id}",
        response_model=BackgroundTaskSpec,
        summary="Patch a background task",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def patch_background_task(
        request: Request, task_id: str, body: BackgroundTaskPatchRequest
    ) -> BackgroundTaskSpec:
        manager = _require_background_manager(request)
        patch = body.model_dump(mode="python", exclude_unset=True)
        try:
            return await manager.update_task(task_id, patch)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/tasks/{task_id}/run",
        response_model=BackgroundRun,
        summary="Queue or wait for a manual task run",
        responses={
            404: _HTTP_ERROR,
            503: _HTTP_ERROR,
            504: {
                "model": BackgroundRunTimeoutResponse,
                "description": "Run did not finish before request timeout",
            },
        },
    )
    async def run_background_task(
        request: Request, task_id: str, body: BackgroundTaskRunRequest | None = None
    ) -> BackgroundRun:
        manager = _require_background_manager(request)
        config = get_config(request)
        run_request = body or BackgroundTaskRunRequest()
        wait_timeout = _background_wait_timeout(config.request_timeout)
        try:
            run = await manager.run_now(
                task_id,
                query=run_request.query,
                wait=run_request.wait,
                timeout_seconds=wait_timeout if run_request.wait else None,
            )
            if not run_request.wait:
                return run
            if run.status not in TERMINAL_RUN_STATUSES:
                if wait_timeout is None:
                    return run
                _raise_run_timeout(run, wait_timeout, config.request_timeout)
            return run
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/tasks/{task_id}/pause",
        response_model=BackgroundStatusResponse,
        summary="Pause scheduled dispatch for a task",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def pause_background_task(
        request: Request, task_id: str
    ) -> BackgroundStatusResponse:
        manager = _require_background_manager(request)
        try:
            await manager.pause_task(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return BackgroundStatusResponse(status="paused")

    @router.post(
        "/tasks/{task_id}/resume",
        response_model=BackgroundStatusResponse,
        summary="Resume scheduled dispatch for a task",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def resume_background_task(
        request: Request, task_id: str
    ) -> BackgroundStatusResponse:
        manager = _require_background_manager(request)
        try:
            await manager.resume_task(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return BackgroundStatusResponse(status="resumed")

    @router.delete(
        "/tasks/{task_id}",
        response_model=BackgroundStatusResponse,
        summary="Delete a background task",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def delete_background_task(
        request: Request,
        task_id: str,
        delete_runs: bool = Query(default=False),
    ) -> BackgroundStatusResponse:
        manager = _require_background_manager(request)
        if not await manager.get_task(task_id):
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        await manager.delete_task(task_id, delete_runs=delete_runs)
        return BackgroundStatusResponse(status="deleted")

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=BackgroundStatusResponse,
        summary="Cancel a queued or running background run",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def cancel_background_run(
        request: Request, run_id: str
    ) -> BackgroundStatusResponse:
        manager = _require_background_manager(request)
        try:
            await manager.cancel_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return BackgroundStatusResponse(status="cancel_requested")

    @router.get(
        "/runs",
        response_model=BackgroundRunsResponse,
        summary="List background runs",
        responses={503: _HTTP_ERROR},
    )
    async def list_background_runs(
        request: Request,
        task_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
    ) -> BackgroundRunsResponse:
        manager = _require_background_manager(request)
        try:
            runs = await manager.list_runs(task_id=task_id, status=status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return BackgroundRunsResponse(runs=runs, total=len(runs))

    @router.get(
        "/runs/{run_id}",
        response_model=BackgroundRun,
        summary="Get background run status",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def get_background_run(request: Request, run_id: str) -> BackgroundRun:
        manager = _require_background_manager(request)
        run = await manager.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return run

    @router.get(
        "/runs/{run_id}/attempts",
        response_model=list[BackgroundAttempt],
        summary="List background run attempts",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def list_background_run_attempts(request: Request, run_id: str):
        manager = _require_background_manager(request)
        await _require_run(manager, run_id)
        return await manager.list_attempts(run_id)

    @router.get(
        "/runs/{run_id}/events",
        response_model=BackgroundRunEventsResponse,
        summary="Replay background run lifecycle events",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def list_background_run_events(
        request: Request, run_id: str
    ) -> BackgroundRunEventsResponse:
        manager = _require_background_manager(request)
        await _require_run(manager, run_id)
        events = await manager.get_run_events(run_id)
        return BackgroundRunEventsResponse(
            run_id=run_id,
            events=events,
            count=len(events),
        )

    @router.get(
        "/runs/{run_id}/workspace",
        response_model=BackgroundRunWorkspaceResponse,
        summary="Inspect background run workspace files",
        responses={404: _HTTP_ERROR, 503: _HTTP_ERROR},
    )
    async def get_background_run_workspace(
        request: Request, run_id: str
    ) -> BackgroundRunWorkspaceResponse:
        manager = _require_background_manager(request)
        try:
            return BackgroundRunWorkspaceResponse(**await manager.get_run_workspace(run_id))
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def _require_background_manager(request: Request):
    manager = get_background_manager(request)
    if manager is None:
        raise HTTPException(status_code=503, detail="Background execution is disabled")
    return manager


async def _require_run(manager, run_id: str) -> BackgroundRun:
    run = await manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


def _raise_run_timeout(
    run: BackgroundRun,
    wait_timeout_seconds: float | None,
    request_timeout_seconds: float | None,
) -> None:
    raise HTTPException(
        status_code=504,
        detail={
            "message": (
                "Background run did not finish within "
                f"{wait_timeout_seconds} seconds"
            ),
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status.value,
            "wait_timeout_seconds": wait_timeout_seconds,
            "request_timeout_seconds": request_timeout_seconds,
        },
    )


def _background_wait_timeout(request_timeout: float | None) -> float | None:
    if request_timeout is None or request_timeout <= 0:
        return None
    return max(request_timeout - 0.25, 0.001)
