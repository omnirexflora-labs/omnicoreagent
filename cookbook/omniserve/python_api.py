#!/usr/bin/env python3
"""
OmniServe - Python API Example (Full Configuration)

=============================================================================
HOW TO RUN
=============================================================================

    cd /path/to/omnicoreagent
    uv run python cookbook/omniserve/python_api.py

=============================================================================
WHAT THIS DEMONSTRATES
=============================================================================

This example shows how to use OmniServe directly from Python with FULL
configuration of ALL available options:

    ✓ Server: host, port, workers, API prefix
    ✓ Docs: Swagger UI, ReDoc
    ✓ CORS: origins, credentials, methods, headers
    ✓ Auth: Bearer token authentication
    ✓ Rate Limit: requests per time window
    ✓ Logging: request logging, log level
    ✓ Timeout: request timeout
    ✓ Tools: Custom local tools

=============================================================================
TEST THE API
=============================================================================

    # Health check (no auth required)
    curl http://localhost:8000/health

    # Prometheus metrics (no auth required)
    curl http://localhost:8000/prometheus

    # Run a query (auth required)
    curl -X POST http://localhost:8000/run/sync \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer my-secret-token" \
        -d '{"query": "What is 2+2?"}'

    # Stream a response (auth required)
    curl -X POST http://localhost:8000/run \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer my-secret-token" \
        -d '{"query": "Explain AI agents"}'

    # List tools
    curl http://localhost:8000/tools \
        -H "Authorization: Bearer my-secret-token"

    # Interactive docs
    open http://localhost:8000/docs

=============================================================================
"""

from omnicoreagent import (  # noqa: E402
    OmniCoreAgent,
    ToolRegistry,
    OmniServe,
    OmniServeConfig,
)
from _bootstrap import model_config, require_llm_api_key  # noqa: E402


# =============================================================================
# STEP 1: Define Tools (Optional)
# =============================================================================

tools = ToolRegistry()


@tools.register_tool("calculate")
def calculate(expression: str) -> dict:
    """Evaluate a math expression like '2 + 2' or 'sqrt(16)'."""
    import math

    try:
        result = eval(
            expression,
            {"__builtins__": {}},
            {
                "sqrt": math.sqrt,
                "sin": math.sin,
                "cos": math.cos,
                "pi": math.pi,
                "abs": abs,
                "round": round,
            },
        )
        return {"expression": expression, "result": result}
    except Exception as e:
        return {"error": str(e)}


@tools.register_tool("get_time")
def get_time() -> dict:
    """Get the current time."""
    from datetime import datetime

    now = datetime.now()
    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "timezone": "local",
    }


# =============================================================================
# STEP 2: Create the Agent
# =============================================================================

agent = OmniCoreAgent(
    name="DemoAgent",
    system_instruction="""You are a helpful assistant with tools.

Available tools:
- calculate: Evaluate math expressions
- get_time: Get current time

Use tools when appropriate.""",
    model_config={
        **model_config(max_tokens=700),
    },
    local_tools=tools,
    debug=False,
    agent_config={
        "workspace_config": {
            "workspace_backend": "local",
            "workspace_dir": "tmp/omniserve_python_workspace",
        }
    },
)


# =============================================================================
# STEP 3: Configure the Server (ALL OPTIONS)
# =============================================================================

config = OmniServeConfig(
    # -------------------------------------------------------------------------
    # SERVER
    # -------------------------------------------------------------------------
    host="0.0.0.0",  # OMNICOREAGENT_SERVE_HOST
    port=8000,  # OMNICOREAGENT_SERVE_PORT
    workers=1,  # OMNICOREAGENT_SERVE_WORKERS
    api_prefix="",  # OMNICOREAGENT_SERVE_API_PREFIX (e.g., "/api/v1")
    # -------------------------------------------------------------------------
    # DOCUMENTATION
    # -------------------------------------------------------------------------
    enable_docs=True,  # OMNICOREAGENT_SERVE_ENABLE_DOCS - Swagger UI at /docs
    enable_redoc=True,  # OMNICOREAGENT_SERVE_ENABLE_REDOC - ReDoc at /redoc
    # -------------------------------------------------------------------------
    # CORS (Cross-Origin Resource Sharing)
    # -------------------------------------------------------------------------
    cors_enabled=True,  # OMNICOREAGENT_SERVE_CORS_ENABLED
    cors_origins=["*"],  # OMNICOREAGENT_SERVE_CORS_ORIGINS - comma-separated
    cors_credentials=True,  # OMNICOREAGENT_SERVE_CORS_CREDENTIALS
    cors_methods=["*"],  # OMNICOREAGENT_SERVE_CORS_METHODS
    cors_headers=["*"],  # OMNICOREAGENT_SERVE_CORS_HEADERS
    # -------------------------------------------------------------------------
    # AUTHENTICATION
    # -------------------------------------------------------------------------
    auth_enabled=True,  # OMNICOREAGENT_SERVE_AUTH_ENABLED
    auth_token="my-secret-token",  # OMNICOREAGENT_SERVE_AUTH_TOKEN
    # -------------------------------------------------------------------------
    # RATE LIMITING
    # -------------------------------------------------------------------------
    rate_limit_enabled=True,  # OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED
    rate_limit_requests=100,  # OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS
    rate_limit_window=60,  # OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW (seconds)
    # -------------------------------------------------------------------------
    # LOGGING
    # -------------------------------------------------------------------------
    request_logging=True,  # OMNICOREAGENT_SERVE_REQUEST_LOGGING
    log_level="INFO",  # OMNICOREAGENT_SERVE_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)
    # -------------------------------------------------------------------------
    # TIMEOUTS
    # -------------------------------------------------------------------------
    request_timeout=300,  # OMNICOREAGENT_SERVE_REQUEST_TIMEOUT (seconds)
)


# =============================================================================
# STEP 4: Create and Start the Server
# =============================================================================

if __name__ == "__main__":
    require_llm_api_key()
    server = OmniServe(
        agent=agent,
        config=config,
        title="Demo Agent API",
        description="OmniServe Python API example with full configuration",
    )

    # Startup banner
    print()
    print("=" * 70)
    print("🚀 OmniServe - Python API Example")
    print("=" * 70)
    print()
    print("ENDPOINTS:")
    print(f"  Server:      http://localhost:{config.port}")
    print(f"  Swagger UI:  http://localhost:{config.port}/docs")
    print(f"  Prometheus:  http://localhost:{config.port}/prometheus")
    print()
    print("CONFIGURATION:")
    print("  ✓ Auth:       Enabled (Bearer token)")
    print(
        f"  ✓ Rate Limit: {config.rate_limit_requests} req / {config.rate_limit_window}s"
    )
    print(f"  ✓ CORS:       {config.cors_origins}")
    print(f"  ✓ Timeout:    {config.request_timeout}s")
    print()
    print("TOOLS:")
    print("  • calculate(expression)")
    print("  • get_time()")
    print()
    print("TEST:")
    print("  curl -X POST http://localhost:8000/run/sync \\")
    print('    -H "Authorization: Bearer my-secret-token" \\')
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"query": "What is 2+2?"}\'')
    print()
    print("=" * 70)
    print()

    server.start()
