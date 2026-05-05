"""
OmniServe Resilience - Retry Logic and Circuit Breaker.

Provides automatic retry for LLM/API failures and circuit breaker
to prevent cascading failures.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Callable, Dict, Optional, TypeVar

from omnicoreagent.core.utils import logger


# =============================================================================
# Retry Logic
# =============================================================================


class RetryStrategy(Enum):
    """Retry backoff strategies."""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    """Maximum number of retry attempts."""

    base_delay: float = 1.0
    """Base delay in seconds between retries."""

    max_delay: float = 30.0
    """Maximum delay in seconds between retries."""

    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    """Backoff strategy to use."""

    jitter: float = 0.1
    """Random jitter factor (0-1) to add to delay."""

    retryable_exceptions: tuple = (
        ConnectionError,
        TimeoutError,
        asyncio.TimeoutError,
    )
    """Exceptions that should trigger a retry."""

    retryable_status_codes: tuple = (429, 500, 502, 503, 504)
    """HTTP status codes that should trigger a retry."""


def calculate_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """
    Calculate delay for a retry attempt.

    Args:
        attempt: Current attempt number (0-indexed)
        config: Retry configuration

    Returns:
        Delay in seconds
    """
    import random

    if config.strategy == RetryStrategy.FIXED:
        delay = config.base_delay
    elif config.strategy == RetryStrategy.LINEAR:
        delay = config.base_delay * (attempt + 1)
    else:  # EXPONENTIAL
        delay = config.base_delay * (2**attempt)

    # Apply max delay cap
    delay = min(delay, config.max_delay)

    # Apply jitter
    if config.jitter > 0:
        jitter_range = delay * config.jitter
        delay += random.uniform(-jitter_range, jitter_range)

    return max(0, delay)


T = TypeVar("T")


async def retry_async(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    **kwargs,
) -> T:
    """
    Execute an async function with retry logic.

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        config: Retry configuration
        on_retry: Optional callback called on each retry
        **kwargs: Keyword arguments for func

    Returns:
        Result of func

    Raises:
        Last exception if all retries failed
    """
    config = config or RetryConfig()
    last_exception: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e

            if attempt < config.max_retries:
                delay = calculate_delay(attempt, config)
                logger.warning(
                    f"Retry attempt {attempt + 1}/{config.max_retries} "
                    f"after {delay:.2f}s: {type(e).__name__}: {e}"
                )

                if on_retry:
                    on_retry(attempt + 1, e)

                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {config.max_retries} retries exhausted: "
                    f"{type(e).__name__}: {e}"
                )
        except Exception:
            # Non-retryable exception
            raise

    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic error")


def with_retry(config: Optional[RetryConfig] = None):
    """
    Decorator for adding retry logic to async functions.

    Usage:
        @with_retry(RetryConfig(max_retries=3))
        async def my_function():
            ...
    """
    config = config or RetryConfig()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_async(func, *args, config=config, **kwargs)

        return wrapper

    return decorator


# =============================================================================
# Circuit Breaker
# =============================================================================


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5
    """Number of failures before opening circuit."""

    success_threshold: int = 2
    """Number of successes in half-open state to close circuit."""

    timeout: float = 30.0
    """Time in seconds before attempting to close circuit."""

    excluded_exceptions: tuple = ()
    """Exceptions that should not count as failures."""


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker '{name}' is open. Retry after {retry_after:.1f}s"
        )


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for preventing cascading failures.

    Usage:
        breaker = CircuitBreaker("llm-api")

        async with breaker:
            result = await call_llm_api()
    """

    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, potentially transitioning from OPEN to HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.config.timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(f"Circuit breaker '{self.name}' transitioning to HALF_OPEN")
        return self._state

    def _record_success(self):
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                logger.info(f"Circuit breaker '{self.name}' CLOSED (recovered)")
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def _record_failure(self, exception: Exception):
        """Record a failed call."""
        # Check if exception is excluded
        if isinstance(exception, self.config.excluded_exceptions):
            return

        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(f"Circuit breaker '{self.name}' OPEN (still failing)")
        elif self._failure_count >= self.config.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker '{self.name}' OPEN after "
                f"{self._failure_count} failures"
            )

    async def __aenter__(self):
        """Enter the circuit breaker context."""
        state = self.state

        if state == CircuitState.OPEN:
            retry_after = self._last_failure_time + self.config.timeout - time.time()
            raise CircuitBreakerOpenError(self.name, max(0, retry_after))

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the circuit breaker context."""
        if exc_type is None:
            self._record_success()
        elif exc_type is not CircuitBreakerOpenError:
            self._record_failure(exc_val)

        # Don't suppress exceptions
        return False

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Call a function through the circuit breaker.

        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of func

        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        async with self:
            return await func(*args, **kwargs)


# =============================================================================
# Combined Resilience Wrapper
# =============================================================================


async def resilient_call(
    func: Callable[..., T],
    *args,
    retry_config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    **kwargs,
) -> T:
    """
    Execute a function with both retry and circuit breaker protection.

    Args:
        func: Async function to execute
        *args: Positional arguments
        retry_config: Optional retry configuration
        circuit_breaker: Optional circuit breaker
        **kwargs: Keyword arguments

    Returns:
        Result of func
    """

    async def wrapped():
        if circuit_breaker:
            async with circuit_breaker:
                return await func(*args, **kwargs)
        else:
            return await func(*args, **kwargs)

    if retry_config:
        return await retry_async(wrapped, config=retry_config)
    else:
        return await wrapped()


# Global circuit breakers for common services
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """
    Get or create a named circuit breaker.

    Args:
        name: Name of the circuit breaker
        config: Optional configuration

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            config=config or CircuitBreakerConfig(),
        )
    return _circuit_breakers[name]


def get_llm_circuit_breaker() -> CircuitBreaker:
    """Get the circuit breaker for LLM API calls."""
    return get_circuit_breaker(
        "llm-api",
        CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout=60.0,
        ),
    )
