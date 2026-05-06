"""
OmniServe - Main Server Class.

The primary entry point for turning an OmniCoreAgent into a production-ready
FastAPI server.
"""

import time
from typing import TYPE_CHECKING, Any, Optional

from fastapi import FastAPI

from omnicoreagent.core.utils import logger

from .config import OmniServeConfig
from .lifespan import agent_lifespan
from .middleware import setup_all_middleware
from .routes import create_agent_router

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
    ):
        """
        Initialize OmniServe.

        Args:
            agent: The OmniCoreAgent instance to serve
            config: Optional server configuration
            title: Optional API title (defaults to agent name)
            description: Optional API description
        """
        self.agent = agent
        self.config = config or OmniServeConfig()
        self.title = title or f"{agent.name} API"
        self.description = description or (
            f"OmniServe API for {agent.name}. "
            "Powered by OmniCoreAgent framework."
        )

        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        """
        Create and configure the FastAPI application.

        Returns:
            Configured FastAPI application
        """
        # Create FastAPI app with lifespan
        app = FastAPI(
            title=self.title,
            description=self.description,
            version="1.0.0",
            lifespan=agent_lifespan,
            docs_url="/docs" if self.config.enable_docs else None,
            redoc_url="/redoc" if self.config.enable_redoc else None,
        )

        # Store agent and config in app state
        app.state.agent = self.agent
        app.state.config = self.config
        app.state.start_time = time.time()

        # Setup middleware
        setup_all_middleware(app, self.config)

        # Setup observability metrics
        from .observability import setup_observability
        setup_observability(app, self.config, service_name=self.agent.name)

        # Include routes with optional prefix
        router = create_agent_router()
        app.include_router(router, prefix=self.config.api_prefix)

        logger.info(
            f"OmniServe: Created FastAPI app for agent '{self.agent.name}'"
        )

        return app

    def start(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        reload: bool = False,
        workers: Optional[int] = None,
    ) -> None:
        """
        Start the server (blocking).

        Args:
            host: Host to bind to (overrides config)
            port: Port to bind to (overrides config)
            reload: Enable auto-reload for development
            workers: Number of worker processes (overrides config)
        
        Note:
            Reload mode is not supported when using OmniServe directly.
            For hot-reload during development, use:
                uvicorn your_module:app --reload
        """
        import uvicorn

        final_host = host or self.config.host
        final_port = port or self.config.port
        final_workers = workers or self.config.workers

        logger.info(
            f"OmniServe: Starting server at http://{final_host}:{final_port}"
        )
        logger.info(
            f"OmniServe: Swagger UI available at http://{final_host}:{final_port}/docs"
        )

        # Note: reload requires an import string, not an app instance
        # When using OmniServe class directly, we cannot support reload
        if reload:
            logger.warning(
                "OmniServe: Hot reload is not supported with dynamic agent loading. "
                "The server will start without reload. "
                "For hot-reload, create a module with 'app = OmniServe(agent).app' "
                "and run: uvicorn your_module:app --reload"
            )

        uvicorn.run(
            self.app,
            host=final_host,
            port=final_port,
            reload=False,  # Cannot use reload with app instance
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
