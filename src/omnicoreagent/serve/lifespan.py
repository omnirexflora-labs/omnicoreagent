"""
OmniServe Lifespan Manager.

Async context manager for agent lifecycle management.
Handles initialization, MCP server connections, and cleanup.
"""

import sys
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from omnicoreagent.core.logging import logger

if TYPE_CHECKING:
    from omnicoreagent.background import BackgroundAgentManager
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    BackgroundAgentManager = Any
    AgentType = Any


@asynccontextmanager
async def agent_lifespan(app: FastAPI):
    """
    Async context manager for agent lifecycle.

    Handles:
    - OmniCoreAgent MCP server connections
    - Cleanup on shutdown

    Usage:
        app = FastAPI(lifespan=agent_lifespan)
        app.state.agent = my_agent
    """
    agent: AgentType = app.state.agent
    config = app.state.config
    background_manager: BackgroundAgentManager | None = getattr(
        app.state, "background_manager", None
    )
    agent_name = getattr(agent, "name", "UnknownAgent")

    logger.info(f"OmniServe: Starting up agent '{agent_name}'...")

    # Record start time for uptime tracking
    app.state.start_time = time.time()
    app.state.omniserve_startup_complete = False

    try:
        if hasattr(agent, "connect_mcp_servers"):
            await agent.connect_mcp_servers()

        if background_manager is not None:
            await background_manager.initialize()
            await background_manager.register_agent(
                config.background_agent_id,
                agent,
                replace=True,
            )
            if config.background_start_worker:
                await background_manager.start()

        app.state.omniserve_startup_complete = True
        logger.info(f"OmniServe: Agent '{agent_name}' is ready")

        yield

    finally:
        app.state.omniserve_startup_complete = False
        logger.info(f"OmniServe: Shutting down agent '{agent_name}'...")

        cleanup_error: BaseException | None = None
        active_exception = sys.exc_info()[0] is not None

        if background_manager is not None:
            try:
                await background_manager.shutdown()
            except Exception as exc:
                cleanup_error = exc
                logger.error(f"OmniServe: Background manager shutdown failed: {exc}")

        if hasattr(agent, "cleanup"):
            try:
                await agent.cleanup()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
                logger.error(f"OmniServe: Agent cleanup failed: {exc}")

        logger.info(f"OmniServe: Agent '{agent_name}' cleanup complete")
        if cleanup_error is not None and not active_exception:
            raise cleanup_error
