"""FastAPI application factory for OmniServe."""

import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from omnicoreagent.core.logging import logger

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
) -> FastAPI:
    """Create and configure the FastAPI application for one agent."""
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
        config=config,
        background_manager=background_manager,
    )

    setup_all_middleware(app, config)
    setup_metrics(app, config)

    app.include_router(create_agent_router(config), prefix=config.api_prefix)

    logger.info(f"OmniServe: Created FastAPI app for agent '{get_agent_name(agent)}'")
    return app


def _package_version() -> str:
    try:
        return version("omnicoreagent")
    except PackageNotFoundError:
        return "0+unknown"


def _build_background_manager(
    *,
    config: OmniServeConfig,
    background_manager: Any | None,
) -> Any | None:
    if not config.background_enabled:
        return None
    if background_manager is not None:
        return background_manager

    from omnicoreagent.background import BackgroundAgentManager

    return BackgroundAgentManager(task_store=config.background_task_store_config())
