"""FastAPI application factory for OmniServe."""

import time
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
) -> FastAPI:
    """Create and configure the FastAPI application for one agent."""
    app = FastAPI(
        title=title,
        description=description,
        version="1.0.0",
        lifespan=agent_lifespan,
        docs_url="/docs" if config.enable_docs else None,
        redoc_url="/redoc" if config.enable_redoc else None,
    )

    app.state.agent = agent
    app.state.config = config
    app.state.start_time = time.time()

    setup_all_middleware(app, config)
    setup_metrics(app, config)

    app.include_router(create_agent_router(), prefix=config.api_prefix)

    logger.info(f"OmniServe: Created FastAPI app for agent '{get_agent_name(agent)}'")
    return app
