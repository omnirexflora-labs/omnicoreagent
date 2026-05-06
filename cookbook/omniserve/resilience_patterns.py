#!/usr/bin/env python3
"""
OmniServe v0.0.1 - Resilience Patterns Example

=============================================================================
HOW TO RUN
=============================================================================

    cd /path/to/omnicoreagent
    uv run python cookbook/omniserve/resilience_patterns.py

=============================================================================
WHAT THIS DEMONSTRATES
=============================================================================

Production resilience patterns for AI agent APIs:

1. RETRY LOGIC
   - Automatic retry with exponential backoff
   - Configurable max retries, delays, jitter
   - Use for LLM API calls, external services

2. CIRCUIT BREAKER
   - Fail-fast when services are down
   - Prevents cascading failures
   - States: CLOSED → OPEN → HALF_OPEN → CLOSED

3. COMBINING PATTERNS
   - Retry inside circuit breaker
   - Full protection for production APIs

=============================================================================
"""

from dotenv import load_dotenv

load_dotenv()

from omnicoreagent import (  # noqa: E402
    OmniCoreAgent,
    ToolRegistry,
    OmniServe,
    OmniServeConfig,
    # Resilience utilities
    RetryConfig,
    RetryStrategy,
    CircuitBreaker,
    CircuitBreakerConfig,
    with_retry,
    retry_async,
    get_metrics,
)
from omnicoreagent.serve.resilience import CircuitBreakerOpenError  # noqa: E402


# =============================================================================
# EXAMPLE 1: RETRY WITH DECORATOR
# =============================================================================

# Configure retry behavior
retry_config = RetryConfig(
    max_retries=3,  # Try up to 3 times
    base_delay=1.0,  # Start with 1 second delay
    max_delay=30.0,  # Cap delay at 30 seconds
    strategy=RetryStrategy.EXPONENTIAL,  # Exponential backoff
    jitter=True,  # Add randomness to prevent thundering herd
)


@with_retry(retry_config)
async def call_external_api(url: str) -> dict:
    """
    Example: External API call with automatic retry.

    If this function raises an exception, it will be retried
    up to 3 times with exponential backoff.
    """
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10)
        return response.json()


# =============================================================================
# EXAMPLE 2: RETRY WITH FUNCTION (more control)
# =============================================================================


async def call_llm_with_retry(prompt: str) -> str:
    """
    Example: LLM call with retry using the function directly.

    This gives more control over the retry behavior.
    """

    async def _call():
        # Your LLM API call here
        # This is a placeholder
        return f"Response to: {prompt}"

    return await retry_async(
        _call,
        config=RetryConfig(
            max_retries=5,
            base_delay=0.5,
            strategy=RetryStrategy.EXPONENTIAL,
        ),
    )


# =============================================================================
# EXAMPLE 3: CIRCUIT BREAKER
# =============================================================================

# Configure circuit breaker
circuit_config = CircuitBreakerConfig(
    failure_threshold=5,  # Open after 5 consecutive failures
    success_threshold=2,  # Close after 2 successes in half-open state
    timeout=60.0,  # Stay open for 60 seconds before trying again
)

# Create circuit breaker instance
llm_circuit = CircuitBreaker(
    name="llm-api",
    config=circuit_config,
)

# Also available for external APIs
external_api_circuit = CircuitBreaker(
    name="external-api",
    config=CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=1,
        timeout=30.0,
    ),
)


async def call_with_circuit_breaker() -> dict:
    """
    Example: Protected call using circuit breaker.

    States:
    - CLOSED: Normal operation, calls go through
    - OPEN: Too many failures, calls fail immediately
    - HALF_OPEN: Testing if service is back, limited calls allowed
    """
    async with llm_circuit:
        # Your risky call here
        # If this fails, circuit breaker tracks it
        # If too many failures, circuit opens and calls fail fast
        return {"status": "success", "message": "Call completed"}


async def call_with_circuit_breaker_fallback() -> dict:
    """
    Example: Circuit breaker with fallback logic.
    """
    try:
        async with external_api_circuit:
            # Risky call
            return {"status": "success", "data": "from_api"}
    except CircuitBreakerOpenError:
        # Circuit is open - use fallback
        return {"status": "fallback", "data": "cached_response"}


# =============================================================================
# EXAMPLE 4: COMBINING RETRY + CIRCUIT BREAKER
# =============================================================================


async def robust_api_call(url: str) -> dict:
    """
    Example: Full production pattern - retry inside circuit breaker.

    The circuit breaker wraps the retry logic:
    - If the circuit is open, we fail fast (no retries)
    - If the circuit is closed, we retry on failures
    - If retries exhaust, that counts as a circuit failure
    """
    try:
        async with external_api_circuit:
            # Retry inside the circuit breaker
            return await retry_async(
                lambda: _make_request(url),
                config=RetryConfig(max_retries=3, base_delay=1.0),
            )
    except CircuitBreakerOpenError:
        return {"error": "Service unavailable", "circuit": "open"}


async def _make_request(url: str) -> dict:
    """Internal helper for making the actual request."""
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10)
        return response.json()


# =============================================================================
# EXAMPLE 5: USING METRICS
# =============================================================================


def demonstrate_metrics():
    """
    Example: Accessing and recording custom metrics.
    """
    metrics = get_metrics()

    # Record custom metrics
    metrics.increment("custom_api_calls")
    metrics.increment("custom_api_calls", 5)  # Increment by 5

    # Record timing
    metrics.observe("custom_latency", 0.123)  # 123ms

    # View current metrics
    print("Current metrics:")
    print(f"  Counters: {dict(metrics.counters)}")
    print(f"  Histograms: {list(metrics.histograms.keys())}")


# =============================================================================
# AGENT WITH RESILIENT TOOLS
# =============================================================================

tools = ToolRegistry()


@tools.register_tool("fetch_data")
async def fetch_data(source: str) -> dict:
    """
    Fetch data from an external source with resilience.

    This tool demonstrates using retry and circuit breaker
    for reliable external API calls.
    """
    try:
        async with external_api_circuit:
            result = await retry_async(
                lambda: _fetch_from_source(source),
                config=RetryConfig(max_retries=3),
            )
            return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _fetch_from_source(source: str) -> str:
    """Simulated fetch - replace with real implementation."""
    return f"Data from {source}"


# =============================================================================
# CREATE AGENT AND SERVER
# =============================================================================

agent = OmniCoreAgent(
    name="ResilientAgent",
    system_instruction="""You are an AI assistant with resilient external integrations.

Available tools:
- fetch_data(source): Fetch data with automatic retry and circuit breaker protection

Your API calls are protected against failures.""",
    model_config={
        "provider": "gemini",
        "model": "gemini-2.0-flash",
    },
    local_tools=tools,
    debug=False,
)

config = OmniServeConfig(
    port=8000,
    auth_enabled=True,
    auth_token="resilience-demo",
    rate_limit_enabled=True,
    rate_limit_requests=100,
)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print()
    print("=" * 70)
    print("🛡️  OmniServe v0.0.1 - Resilience Patterns Demo")
    print("=" * 70)
    print()
    print("RESILIENCE UTILITIES (import from omnicoreagent):")
    print()
    print("  RETRY:")
    print("    from omnicoreagent import RetryConfig, with_retry, retry_async")
    print("    @with_retry(RetryConfig(max_retries=3))")
    print("    async def my_function(): ...")
    print()
    print("  CIRCUIT BREAKER:")
    print("    from omnicoreagent import CircuitBreaker, CircuitBreakerConfig")
    print(
        "    breaker = CircuitBreaker('api', CircuitBreakerConfig(failure_threshold=5))"
    )
    print("    async with breaker:")
    print("        result = await risky_call()")
    print()
    print("  METRICS:")
    print("    from omnicoreagent import get_metrics")
    print("    metrics = get_metrics()")
    print("    metrics.increment('my_counter')")
    print()
    print("-" * 70)

    # Demo metrics
    demonstrate_metrics()

    print("-" * 70)
    print()
    print("Starting server...")
    print(f"  Server:     http://localhost:{config.port}")
    print("  Auth Token: resilience-demo")
    print()
    print("=" * 70)
    print()

    server = OmniServe(
        agent=agent,
        config=config,
        title="Resilient Agent API",
        description="API with retry, circuit breaker, and metrics",
    )
    server.start()
