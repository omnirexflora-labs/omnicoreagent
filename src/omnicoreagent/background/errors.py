"""Typed errors for durable background execution."""


class BackgroundAgentError(Exception):
    """Base error for background execution."""


class AgentAlreadyRegisteredError(BackgroundAgentError):
    """Raised when registering an existing agent without replacement."""


class AgentNotFoundError(BackgroundAgentError):
    """Raised when an agent cannot be found."""


class TaskAlreadyRegisteredError(BackgroundAgentError):
    """Raised when registering an existing task without replacement."""


class TaskNotFoundError(BackgroundAgentError):
    """Raised when a task cannot be found or cannot be run."""


class RunNotFoundError(BackgroundAgentError):
    """Raised when a run cannot be found."""


class InvalidScheduleError(BackgroundAgentError, ValueError):
    """Raised when schedule configuration is invalid."""


class InvalidTaskStoreError(BackgroundAgentError):
    """Raised when task-store configuration is invalid."""


class TaskStoreError(BackgroundAgentError):
    """Raised when task-store operations fail."""


class RunLeaseError(BackgroundAgentError):
    """Raised when a run lease cannot be acquired or verified."""


class RunCancelledError(BackgroundAgentError):
    """Raised when a run is cancelled."""


class RunTimeoutError(BackgroundAgentError):
    """Raised when a run times out."""


class RunExecutionError(BackgroundAgentError):
    """Raised when a run execution fails."""
