#!/usr/bin/env python3
"""
OmniServe v0.0.1 - CLI Agent Example

=============================================================================
HOW TO RUN (CLI)
=============================================================================

    cd /path/to/omnicoreagent

    # Basic run
    omniserve run --agent cookbook/omniserve/cli_agent.py

    # With authentication
    omniserve run --agent cookbook/omniserve/cli_agent.py --auth-token secret123

    # With rate limiting
    omniserve run --agent cookbook/omniserve/cli_agent.py --rate-limit 100

    # Full options
    omniserve run \
        --agent cookbook/omniserve/cli_agent.py \
        --host 0.0.0.0 \
        --port 8000 \
        --auth-token my-secret-token \
        --rate-limit 100 \
        --cors-origins "https://myapp.com,https://api.myapp.com"

=============================================================================
CLI OPTIONS
=============================================================================

    --agent, -a         Path to agent file (required)
    --host, -h          Host to bind (default: 0.0.0.0)
    --port, -p          Port to bind (default: 8000)
    --workers, -w       Worker processes (default: 1)
    --auth-token        Enable auth with this token
    --rate-limit        Rate limit (requests per minute)
    --cors-origins      Comma-separated CORS origins
    --no-docs           Disable Swagger UI

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This file defines an `agent` variable that OmniServe CLI loads.
The CLI handles server configuration via command-line flags.

You can also define a `create_agent()` function instead of an `agent` variable.

=============================================================================
TEST THE API
=============================================================================

    # Health check
    curl http://localhost:8000/health

    # Run a query (if auth enabled)
    curl -X POST http://localhost:8000/run/sync \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer my-secret-token" \
        -d '{"query": "What time is it?"}'

    # Interactive docs
    open http://localhost:8000/docs

=============================================================================
"""

from omnicoreagent import OmniCoreAgent, ToolRegistry  # noqa: E402
from _bootstrap import model_config  # noqa: E402


# =============================================================================
# DEFINE TOOLS
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
    }


@tools.register_tool("search_web")
def search_web(query: str) -> dict:
    """Search the web (demo implementation)."""
    return {
        "query": query,
        "results": [
            {"title": f"Result 1 for: {query}", "url": "https://example.com/1"},
            {"title": f"Result 2 for: {query}", "url": "https://example.com/2"},
        ],
        "note": "Demo implementation - integrate real search API",
    }


# =============================================================================
# DEFINE THE AGENT
# =============================================================================
# The OmniServe CLI looks for:
#   1. An `agent` variable (like below), OR
#   2. A `create_agent()` function that returns an agent
# =============================================================================

agent = OmniCoreAgent(
    name="CLIAgent",
    system_instruction="""You are a helpful AI assistant with access to tools.

Available tools:
- calculate: Evaluate math expressions
- get_time: Get the current time
- search_web: Search the web (demo)

Use tools when helpful. Be concise and clear.""",
    model_config={
        **model_config(max_tokens=700),
    },
    local_tools=tools,
    debug=False,
    agent_config={
        "workspace_config": {
            "workspace_backend": "local",
            "workspace_dir": "tmp/omniserve_cli_workspace",
        }
    },
)


# =============================================================================
# ALTERNATIVE: create_agent() function
# =============================================================================
# def create_agent():
#     """Factory function to create the agent."""
#     return OmniCoreAgent(
#         name="CLIAgent",
#         ...
#     )
