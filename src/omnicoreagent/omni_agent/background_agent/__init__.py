"""
Background Agent System for Self-Flying Automation.

This module provides a comprehensive system for creating and managing
background agents that can execute tasks automatically.
"""

from .background_agents import BackgroundOmniCoreAgent
from .background_agent_manager import BackgroundAgentManager
from .task_registry import TaskRegistry
from .base import BackgroundTaskScheduler

__all__ = [
    "BackgroundOmniCoreAgent",
    "BackgroundAgentManager",
    "TaskRegistry",
    "APSchedulerBackend",
    "BackgroundTaskScheduler",
]


def __getattr__(name: str):
    if name != "APSchedulerBackend":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from omnicoreagent._optional import load_optional

    return load_optional(
        "APScheduler background scheduling",
        "background",
        lambda: __import__(
            "omnicoreagent.omni_agent.background_agent.scheduler_backend",
            fromlist=["APSchedulerBackend"],
        ).APSchedulerBackend,
    )
