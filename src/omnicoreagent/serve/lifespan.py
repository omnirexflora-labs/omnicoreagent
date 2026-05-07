"""
OmniServe Lifespan Manager.

Async context manager for agent lifecycle management.
Handles initialization, MCP server connections, and cleanup.
"""

import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from omnicoreagent.core.logging import logger

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
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
    agent_name = getattr(agent, "name", "UnknownAgent")

    logger.info(f"OmniServe: Starting up agent '{agent_name}'...")

    # Record start time for uptime tracking
    app.state.start_time = time.time()

    try:
        if hasattr(agent, "connect_mcp_servers"):
            await agent.connect_mcp_servers()

        logger.info(f"OmniServe: Agent '{agent_name}' is ready")

        yield

    finally:
        logger.info(f"OmniServe: Shutting down agent '{agent_name}'...")

        if hasattr(agent, "cleanup"):
            await agent.cleanup()

        logger.info(f"OmniServe: Agent '{agent_name}' cleanup complete")
