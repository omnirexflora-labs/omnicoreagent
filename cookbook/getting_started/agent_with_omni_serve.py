"""
OmniServe Cookbook Example

This example demonstrates how to turn an OmniCoreAgent into a
production-ready FastAPI server using OmniServe.

Run this script:
    cd /path/to/omnicoreagent
    uv run python cookbook/getting_started/agent_with_omni_serve.py

Then test the endpoints:
    # Health check
    curl http://localhost:8000/health

    # Synchronous run
    curl -X POST http://localhost:8000/run/sync \
        -H "Content-Type: application/json" \
        -d '{"query": "What is 2+2?"}'

    # SSE streaming run
    curl -X POST http://localhost:8000/run \
        -H "Content-Type: application/json" \
        -d '{"query": "Explain machine learning in 3 sentences."}'

    # List tools
    curl http://localhost:8000/tools

    # Get metrics
    curl http://localhost:8000/metrics

Visit http://localhost:8000/docs for the interactive Swagger UI.
"""

from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig  # noqa: E402
from _bootstrap import model_config, require_llm_api_key  # noqa: E402


def main():
    """Run the OmniServe server with a simple agent."""
    require_llm_api_key()

    # Create the agent
    agent = OmniCoreAgent(
        name="DemoAgent",
        system_instruction="""You are a helpful AI assistant.
        Answer questions clearly and concisely.
        When performing calculations, show your work.""",
        model_config=model_config(max_tokens=700),
        debug=False,
    )

    # Configure the server
    config = OmniServeConfig(
        host="0.0.0.0",
        port=8000,
        cors_enabled=True,
        cors_origins=["*"],  # Allow all origins for demo
        enable_docs=True,
        request_logging=True,
        # Uncomment to enable authentication:
        # auth_enabled=True,
        # auth_token="your-secret-token",
    )

    # Create and start the server
    server = OmniServe(
        agent=agent,
        config=config,
        title="Demo Agent API",
        description="A demo API powered by OmniCoreAgent and OmniServe",
    )

    print("\n" + "=" * 60)
    print("🚀 OmniServe Demo")
    print("=" * 60)
    print(f"Server: http://localhost:{config.port}")
    print(f"Swagger UI: http://localhost:{config.port}/docs")
    print(f"ReDoc: http://localhost:{config.port}/redoc")
    print("=" * 60 + "\n")

    # Start the server (blocking)
    server.start()


if __name__ == "__main__":
    main()
