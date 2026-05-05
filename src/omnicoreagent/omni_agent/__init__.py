"""
OmniCoreAgent Package

This package provides the high-level OmniCoreAgent interface and background agent functionality.
"""

from .agent import OmniCoreAgent

__all__ = [
    "OmniCoreAgent",
    "BackgroundOmniCoreAgent",
    "BackgroundAgentManager",
    "TaskRegistry",
    "APSchedulerBackend",
    "BackgroundTaskScheduler",
    "OmniServe",
    "OmniServeConfig",
]

_OPTIONAL_EXPORTS = {
    "BackgroundOmniCoreAgent": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "BackgroundAgentManager": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "TaskRegistry": ("omnicoreagent.omni_agent.background_agent", "background"),
    "APSchedulerBackend": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "BackgroundTaskScheduler": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "OmniServe": ("omnicoreagent.omni_agent.omni_serve", "serve"),
    "OmniServeConfig": ("omnicoreagent.omni_agent.omni_serve", "serve"),
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from omnicoreagent._optional import load_optional

    module_name, extra = _OPTIONAL_EXPORTS[name]
    return load_optional(
        name,
        extra,
        lambda: getattr(__import__(module_name, fromlist=[name]), name),
    )
