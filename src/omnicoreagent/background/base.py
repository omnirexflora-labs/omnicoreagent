"""
Base scheduler interface for background agent system.
"""

from abc import ABC, abstractmethod
from typing import Callable


class BackgroundTaskScheduler(ABC):
    """Base class for background task schedulers."""

    @abstractmethod
    def schedule_task(self, agent_id: str, interval: int, task_fn: Callable, **kwargs):
        """Schedule a task to run at specified intervals."""
        pass

    @abstractmethod
    def remove_task(self, agent_id: str):
        """Remove a scheduled task."""
        pass

    @abstractmethod
    def start(self):
        """Start the scheduler."""
        pass

    @abstractmethod
    def shutdown(self):
        """Shutdown the scheduler."""
        pass
