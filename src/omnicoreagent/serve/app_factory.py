"""FastAPI application factory for OmniServe."""

import time
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from omnicoreagent.governance.errors import GovernanceError

from omnicoreagent.core.logging import logger
from omnicoreagent.core.runtime import construction

from .admission import RunAdmission, ServerBusyError, default_max_concurrent_runs
from .config import OmniServeConfig
from .lifespan import agent_lifespan
from .metrics import setup_metrics
from .middleware import setup_all_middleware
from .routes import create_agent_router
from .state import get_agent_name

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any


def create_omniserve_app(
    *,
    agent: AgentType,
    config: OmniServeConfig,
    title: str,
    description: str,
    background_manager: Any | None = None,
    routers: Sequence[Any] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application for one agent.

    ``routers`` are the application's own (its pages, its own endpoints),
    mounted beside the agent's API on the same origin, behind the same
    middleware; paths named in ``config.public_paths`` need no token.
    """
    app = FastAPI(
        title=title,
        description=description,
        version=_package_version(),
        lifespan=agent_lifespan,
        docs_url="/docs" if config.enable_docs else None,
        redoc_url="/redoc" if config.enable_redoc else None,
    )

    app.state.agent = agent
    app.state.config = config
    app.state.start_time = time.time()
    app.state.omniserve_startup_complete = False
    app.state.background_manager = _build_background_manager(
        agent=agent,
        config=config,
        background_manager=background_manager,
    )

    setup_all_middleware(app, config)
    setup_metrics(app, config)

    limit = config.max_concurrent_runs
    app.state.run_admission = RunAdmission(
        default_max_concurrent_runs() if limit is None else limit,
        config.run_admission_wait_seconds,
    )
    app.state.omniserve_metrics.collectors.append(
        app.state.run_admission.prometheus_lines
    )

    @app.exception_handler(ServerBusyError)
    async def server_busy(request, exc: ServerBusyError):
        # Answered before any work is done for the request, so it is safe to
        # retry whatever the route was.
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(exc.retry_after)},
            content={
                "error": "ServerBusy",
                "message": (
                    f"This server is already running {exc.limit} runs at once "
                    f"and none finished within {exc.waited:.1f} seconds. "
                    "Retry shortly."
                ),
                "max_concurrent_runs": exc.limit,
                "retry_after": exc.retry_after,
            },
        )

    @app.exception_handler(GovernanceError)
    async def policy_refused(request, exc: GovernanceError):
        # The agent's policy also governs what an operator asks of it over
        # HTTP (a background task is a capability too). A refusal, or an ask
        # nobody can answer inside a request, is the caller's answer, not a
        # server error.
        metadata = getattr(exc, "metadata", None) or {}
        # What was refused and by which rules: "Matched ask policy rule."
        # alone left the caller guessing what to change.
        return JSONResponse(
            status_code=403,
            content={
                "error": type(exc).__name__,
                "detail": str(exc),
                "capability": metadata.get("capability"),
                "reason_code": metadata.get("reason_code"),
                "matched_rule_ids": metadata.get("matched_rule_ids") or [],
            },
        )

    app.include_router(create_agent_router(config), prefix=config.api_prefix)
    for router in routers or ():
        app.include_router(router)

    logger.info(f"OmniServe: Created FastAPI app for agent '{get_agent_name(agent)}'")
    return app


def _package_version() -> str:
    try:
        return version("omnicoreagent")
    except PackageNotFoundError:
        return "0+unknown"


def _build_background_manager(
    *,
    agent: AgentType,
    config: OmniServeConfig,
    background_manager: Any | None,
) -> Any | None:
    if not config.background_enabled:
        return None
    if background_manager is not None:
        return background_manager

    from omnicoreagent.background import BackgroundAgentManager

    _ensure_agent_telemetry(agent)
    return BackgroundAgentManager(
        task_store=config.background_task_store_config(),
        telemetry_store=getattr(agent, "telemetry_store", None),
        governance_engine=_resolve_agent_governance_engine(agent),
    )


def _ensure_agent_telemetry(agent: AgentType) -> None:
    ensure_telemetry = getattr(agent, "_ensure_telemetry", None)
    if callable(ensure_telemetry):
        ensure_telemetry()


def _resolve_agent_governance_engine(agent: AgentType) -> Any | None:
    runtime_agent = getattr(agent, "agent", None)
    runtime_engine = getattr(runtime_agent, "governance_engine", None)
    if runtime_engine is not None:
        return runtime_engine

    agent_config = getattr(agent, "agent_config", {}) or {}
    governance_config = agent_config.get("governance_config") or {}
    if not governance_config.get("enabled", False):
        return None
    return construction.build_governance_engine(
        agent_config=agent_config,
        telemetry_recorder=getattr(agent, "telemetry_recorder", None),
    )
