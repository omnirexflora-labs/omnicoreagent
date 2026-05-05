"""
OmniServe Lifespan Manager.

Async context manager for agent lifecycle management.
Handles initialization, MCP server connections, and cleanup.
"""

import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Union

from fastapi import FastAPI

from omnicoreagent.core.utils import logger

if TYPE_CHECKING:
    from omnicoreagent.agent import OmniCoreAgent
    from omnicoreagent.deep_agent import DeepAgent

AgentType = Union["OmniCoreAgent", "DeepAgent"]


@asynccontextmanager
async def agent_lifespan(app: FastAPI):
    """
    Async context manager for agent lifecycle.

    Handles:
    - DeepAgent initialization (if applicable)
    - MCP server connections
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
        # Handle DeepAgent initialization
        if hasattr(agent, "initialize") and hasattr(agent, "is_initialized"):
            if not agent.is_initialized:
                logger.info(f"OmniServe: Initializing DeepAgent '{agent_name}'...")
                await agent.initialize()
        else:
            # OmniCoreAgent - just connect MCP servers
            if hasattr(agent, "connect_mcp_servers"):
                await agent.connect_mcp_servers()

        logger.info(f"OmniServe: Agent '{agent_name}' is ready")

        yield

    finally:
        logger.info(f"OmniServe: Shutting down agent '{agent_name}'...")

        if hasattr(agent, "cleanup"):
            await agent.cleanup()

        logger.info(f"OmniServe: Agent '{agent_name}' cleanup complete")
