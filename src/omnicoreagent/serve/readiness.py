"""Readiness checks for the OmniServe serving boundary."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request

from .state import get_agent, get_agent_name

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any


@dataclass(frozen=True)
class ReadinessState:
    """Computed readiness state for one OmniServe app instance."""

    ready: bool
    agent_name: str
    initialized: bool
    mcp_connected: bool


def evaluate_readiness(request: Request) -> ReadinessState:
    """Return cheap readiness state without executing model or tool work."""
    agent = get_agent(request)
    initialized = _agent_initialized(agent)
    mcp_connected = _mcp_connected(agent)
    startup_complete = bool(
        getattr(request.app.state, "omniserve_startup_complete", False)
    )

    return ReadinessState(
        ready=startup_complete and initialized and mcp_connected,
        agent_name=get_agent_name(agent),
        initialized=initialized,
        mcp_connected=mcp_connected,
    )


def _agent_initialized(agent: AgentType) -> bool:
    return bool(getattr(agent, "_initialized", True))


def _mcp_connected(agent: AgentType) -> bool:
    if not hasattr(agent, "mcp_client"):
        return True
    return getattr(agent, "mcp_client") is not None
