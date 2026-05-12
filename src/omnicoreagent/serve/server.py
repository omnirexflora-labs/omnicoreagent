"""
OmniServe - Main Server Class.

The primary entry point for turning an OmniCoreAgent into a production-ready
FastAPI server.
"""

from typing import TYPE_CHECKING, Any, Optional

from fastapi import FastAPI

from omnicoreagent.core.logging import logger

from .app_factory import create_omniserve_app
from .config import OmniServeConfig
from .state import get_agent_name

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any


class OmniServe:
    """
    Production-ready FastAPI server for OmniCoreAgent.

    Transforms an OmniCoreAgent into a full REST/SSE API server.

    Features:
    - SSE streaming for agent responses (/run)
    - Synchronous JSON responses (/run/sync)
    - Health and readiness endpoints
    - Session management
    - Metrics and tools listing
    - Configurable middleware (CORS, auth, logging)
    - Proper lifecycle management

    Usage:
        from omnicoreagent import OmniCoreAgent, OmniServe

        agent = OmniCoreAgent(
            name="MyAgent",
            system_instruction="You are helpful.",
            model_config={"provider": "openai", "model": "gpt-4o"},
        )

        server = OmniServe(agent)
        server.start(host="0.0.0.0", port=8000)

    For async usage:
        async def main():
            await server.start_async()
    """

    def __init__(
        self,
        agent: AgentType,
        config: Optional[OmniServeConfig] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        background_manager: Any | None = None,
    ):
        """
        Initialize OmniServe.

        Args:
            agent: The OmniCoreAgent instance to serve
            config: Optional server configuration
            title: Optional API title (defaults to agent name)
            description: Optional API description
            background_manager: Optional durable background manager to serve
        """
        self.agent = agent
        self.config = config or OmniServeConfig()
        self.title = title or f"{get_agent_name(agent)} API"
        self.description = description or (
            f"OmniServe API for {get_agent_name(agent)}. Powered by OmniCoreAgent."
        )
        self.background_manager = background_manager

        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        """
        Create and configure the FastAPI application.

        Returns:
            Configured FastAPI application
        """
        return create_omniserve_app(
            agent=self.agent,
            config=self.config,
            title=self.title,
            description=self.description,
            background_manager=self.background_manager,
        )

    def start(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        workers: Optional[int] = None,
    ) -> None:
        """
        Start the server (blocking).

        Args:
            host: Host to bind to (overrides config)
            port: Port to bind to (overrides config)
            workers: Number of worker processes (overrides config)
        """
        import uvicorn

        final_host = host or self.config.host
        final_port = port or self.config.port
        final_workers = workers or self.config.workers

        logger.info(f"OmniServe: Starting server at http://{final_host}:{final_port}")
        logger.info(
            f"OmniServe: Swagger UI available at http://{final_host}:{final_port}/docs"
        )

        uvicorn.run(
            self.app,
            host=final_host,
            port=final_port,
            workers=final_workers,
            log_level=self.config.log_level.lower(),
        )

    async def start_async(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        """
        Start the server asynchronously.

        Useful for embedding in existing async applications.

        Args:
            host: Host to bind to (overrides config)
            port: Port to bind to (overrides config)
        """
        import uvicorn

        final_host = host or self.config.host
        final_port = port or self.config.port

        logger.info(
            f"OmniServe: Starting async server at http://{final_host}:{final_port}"
        )

        config = uvicorn.Config(
            self.app,
            host=final_host,
            port=final_port,
            log_level=self.config.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    def get_app(self) -> FastAPI:
        """
        Get the FastAPI application instance.

        Useful for mounting in existing applications or for testing.

        Returns:
            The FastAPI application
        """
        return self.app
