"""
OmniServe - Production-ready FastAPI server for OmniCoreAgent.

Transforms an OmniCoreAgent into a REST/SSE API server with:
- SSE streaming for agent responses
- Health and readiness endpoints
- Session management
- Metrics and tools listing
- Configurable middleware (CORS, auth, logging, rate limiting)
- Prometheus metrics endpoint
- Agent event streaming and compact event summaries
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

CLI Usage:
    omniserve quickstart --provider gemini --model gemini-2.0-flash
    omniserve run --agent my_agent.py --port 8000

Extensibility:
    Users can import OmniServeConfig for server configuration.
"""

from .server import OmniServe
from .config import OmniServeConfig

__all__ = [
    "OmniServe",
    "OmniServeConfig",
]
