from __future__ import annotations as _annotations

from copy import copy
from dataclasses import dataclass, field


class UsageLimitExceeded(Exception):
    pass


@dataclass
class Usage:
    """LLM usage associated with a request or run.

    Responsibility for calculating usage is on the model; the system simply sums the usage information across requests.

    You'll need to look up the documentation of the model you're using to convert usage to monetary costs.
    """

    requests: int = 0
    """Number of requests made to the LLM API."""
    request_tokens: int | None = None
    """Tokens used in processing requests."""
    response_tokens: int | None = None
    """Tokens used in generating responses."""
    total_tokens: int | None = None
    """Total tokens used in the whole run, should generally be equal to `request_tokens + response_tokens`."""
    total_time: float | None = 0.0
    """Total time taken for the whole run in seconds."""
    details: dict[str, int] | None = field(default_factory=dict)

    """Any extra details returned by the model."""

    def __post_init__(self) -> None:
        """Initialize details as an empty dict if None."""
        if self.details is None:
            self.details = {}

        if (
            self.total_tokens is None
            and self.request_tokens is not None
            and self.response_tokens is not None
        ):
            self.total_tokens = self.request_tokens + self.response_tokens

    def incr(self, incr_usage: Usage, *, requests: int = 0) -> None:
        """Increment the usage in place.

        Args:
            incr_usage: The usage to increment by.
            requests: The number of requests to increment by in addition to `incr_usage.requests`.
        """
        self.requests += incr_usage.requests + requests

        for f in ("request_tokens", "response_tokens"):
            self_value = getattr(self, f)
            other_value = getattr(incr_usage, f)
            if other_value is not None:
                setattr(self, f, (self_value or 0) + other_value)

        if incr_usage.total_tokens is not None:
            self.total_tokens = (self.total_tokens or 0) + incr_usage.total_tokens
        elif self.request_tokens is not None and self.response_tokens is not None:
            self.total_tokens = self.request_tokens + self.response_tokens

        if incr_usage.total_time is not None:
            self.total_time = (self.total_time or 0.0) + incr_usage.total_time

        if incr_usage.details:
            for key, value in incr_usage.details.items():
                self.details[key] = self.details.get(key, 0) + value

    def __add__(self, other: Usage) -> Usage:
        """Add two Usages together.

        This is provided so it's trivial to sum usage information from multiple requests and runs.
        """
        new_usage = copy(self)
        new_usage.incr(other)
        return new_usage


@dataclass
class UsageLimits:
    """Limits on model usage.

    The request count is tracked by the system, and the request limit is checked before each request to the model.
    Token counts are provided in responses from the model, and the token limits are checked after each response.

    Each of the limits can be set to `None` to disable that limit.
    """

    request_limit: int | None = 50
    """The maximum number of requests allowed to the model."""
    request_tokens_limit: int | None = None
    """The maximum number of tokens allowed in requests to the model."""
    response_tokens_limit: int | None = None
    """The maximum number of tokens allowed in responses from the model."""
    total_tokens_limit: int | None = None
    """The maximum number of tokens allowed in requests and responses combined."""

    def __post_init__(self) -> None:
        """Validate limits upon initialization."""
        if self.request_limit is not None and self.request_limit < 0:
            raise ValueError("request_limit must be non-negative if specified")

        for limit_name in (
            "request_tokens_limit",
            "response_tokens_limit",
            "total_tokens_limit",
        ):
            limit_value = getattr(self, limit_name)
            if limit_value is not None and limit_value < 0:
                raise ValueError(f"{limit_name} must be non-negative if specified")

    def has_token_limits(self) -> bool:
        """Returns `True` if this instance places any limits on token counts.

        If this returns `False`, the `check_tokens` method will never raise an error.

        This is useful because if we have token limits, we need to check them after receiving each streamed message.
        If there are no limits, we can skip that processing in the streaming response iterator.
        """
        return any(
            limit is not None and limit > 0
            for limit in (
                self.request_tokens_limit,
                self.response_tokens_limit,
                self.total_tokens_limit,
            )
        )

    def remaining_tokens(self, usage: Usage) -> dict[str, int | None]:
        """Calculate remaining tokens for each limit.

        Args:
            usage: The current usage to check against limits

        Returns:
            Dictionary with remaining tokens for each limit type
        """
        result = {}

        if self.request_tokens_limit is not None:
            used = usage.request_tokens or 0
            result["request_tokens"] = self.request_tokens_limit - used

        if self.response_tokens_limit is not None:
            used = usage.response_tokens or 0
            result["response_tokens"] = self.response_tokens_limit - used

        if self.total_tokens_limit is not None:
            used = usage.total_tokens or 0
            result["total_tokens"] = self.total_tokens_limit - used

        return result["total_tokens"]

    def check_before_request(self, usage: Usage) -> None:
        """Raises a `UsageLimitExceeded` exception if the next request would exceed the request_limit.

        Args:
            usage: The current usage to check against limits

        Raises:
            UsageLimitExceeded: If the next request would exceed the request_limit
        """
        if self.request_limit == 0:
            return

        if self.request_limit is not None and usage.requests >= self.request_limit:
            raise UsageLimitExceeded(
                f"The next request would exceed the request_limit of {self.request_limit}. "
                f"Current requests: {usage.requests}"
            )

    def check_tokens(self, usage: Usage) -> None:
        """Raises a `UsageLimitExceeded` exception if the usage exceeds any of the token limits.

        Args:
            usage: The current usage to check against limits

        Raises:
            UsageLimitExceeded: If any token limit is exceeded
        """
        if not self.has_token_limits():
            return

        request_tokens = usage.request_tokens or 0
        if (
            self.request_tokens_limit is not None
            and request_tokens > self.request_tokens_limit
        ):
            raise UsageLimitExceeded(
                f"Exceeded the request_tokens_limit of {self.request_tokens_limit}. "
                f"Current request tokens: {request_tokens}"
            )

        response_tokens = usage.response_tokens or 0
        if (
            self.response_tokens_limit is not None
            and response_tokens > self.response_tokens_limit
        ):
            raise UsageLimitExceeded(
                f"Exceeded the response_tokens_limit of {self.response_tokens_limit}. "
                f"Current response tokens: {response_tokens}"
            )

        total_tokens = usage.total_tokens or 0
        if (
            self.total_tokens_limit is not None
            and self.total_tokens_limit > 0
            and total_tokens > self.total_tokens_limit
        ):
            raise UsageLimitExceeded(
                f"Exceeded the total_tokens_limit of {self.total_tokens_limit}. "
                f"Current total tokens: {total_tokens}"
            )


session_stats = {
    "used_requests": 0,
    "used_tokens": 0,
    "remaining_requests": 0,
    "remaining_tokens": 0,
    "request_tokens": 0,
    "response_tokens": 0,
    "total_tokens": 0,
}
usage = Usage()
