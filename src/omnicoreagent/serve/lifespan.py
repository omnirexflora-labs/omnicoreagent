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

from .orphan_sweep import OrphanSweeper

if TYPE_CHECKING:
    from omnicoreagent.background import BackgroundAgentManager
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    BackgroundAgentManager = Any
    AgentType = Any


async def _warm_model_client(agent: Any) -> None:
    connection = getattr(agent, "llm_connection", None)
    warm_up = getattr(connection, "warm_up", None)
    if warm_up is not None:
        await warm_up()


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
        # The provider client costs seconds to import; the server pays that
        # now, in a thread, rather than whichever request arrives first.
        await _warm_model_client(agent)

        if background_manager is not None:
            await background_manager.initialize()
            await background_manager.register_agent(
                config.background_agent_id,
                agent,
                replace=True,
            )
            if config.background_start_worker:
                await background_manager.start()

        app.state.orphan_sweeper = None
        if config.orphan_sweep_enabled and hasattr(agent, "claim_orphaned_runs"):
            # Runs whose process died, and runs a person decided that no client
            # resumed, are resumed here; they otherwise wait for someone to
            # call resume (see orphan_sweep).
            app.state.orphan_sweeper = OrphanSweeper(
                agent,
                interval_seconds=config.orphan_sweep_interval_seconds,
                max_concurrent=config.orphan_sweep_max_concurrent,
                max_recoveries=config.orphan_sweep_max_recoveries,
                decided_grace_seconds=config.orphan_sweep_decided_grace_seconds,
                run_timeout=config.request_timeout,
            )
            await app.state.orphan_sweeper.start()

        app.state.omniserve_startup_complete = True
        logger.info(f"OmniServe: Agent '{agent_name}' is ready")

        yield

    finally:
        app.state.omniserve_startup_complete = False
        logger.info(f"OmniServe: Shutting down agent '{agent_name}'...")

        cleanup_error: BaseException | None = None
        active_exception = sys.exc_info()[0] is not None

        sweeper = getattr(app.state, "orphan_sweeper", None)
        if sweeper is not None:
            await sweeper.stop()

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
