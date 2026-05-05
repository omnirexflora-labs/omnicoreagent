"""
OmniServe - Production-ready FastAPI server for OmniCoreAgent and DeepAgent.

Transforms any agent into a full REST/SSE API server with:
- SSE streaming for agent responses
- Health and readiness endpoints
- Session management
- Metrics and tools listing
- Configurable middleware (CORS, auth, logging, rate limiting)
- Prometheus metrics endpoint
- OpenTelemetry tracing support
- Retry logic and circuit breaker for resilience
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
    Users can import individual components to extend functionality:
    - OmniServeConfig: Server configuration
    - RetryConfig, with_retry: Retry logic
    - CircuitBreaker, get_circuit_breaker: Circuit breaker pattern
    - get_metrics: Access Prometheus metrics
"""

from .server import OmniServe
from .config import OmniServeConfig

# Resilience utilities for user extension
from .resilience import (
    RetryConfig,
    RetryStrategy,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    with_retry,
    retry_async,
    resilient_call,
    get_circuit_breaker,
    get_llm_circuit_breaker,
)

# Observability utilities
from .observability import get_metrics, SimpleMetrics

__all__ = [
    # Main classes
    "OmniServe",
    "OmniServeConfig",
    # Resilience
    "RetryConfig",
    "RetryStrategy",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "with_retry",
    "retry_async",
    "resilient_call",
    "get_circuit_breaker",
    "get_llm_circuit_breaker",
    # Observability
    "get_metrics",
    "SimpleMetrics",
]

