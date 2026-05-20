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
    configured_servers = _configured_mcp_server_names(agent)
    if not configured_servers:
        return True

    mcp_client = getattr(agent, "mcp_client", None)
    if mcp_client is None:
        return False

    sessions = getattr(mcp_client, "sessions", None)
    if not isinstance(sessions, dict):
        return False

    requested_to_actual = getattr(mcp_client, "added_servers_names", {}) or {}
    for server_name in configured_servers:
        actual_name = requested_to_actual.get(server_name, server_name)
        session = sessions.get(actual_name)
        if not _session_connected(session):
            return False
    return True


def _configured_mcp_server_names(agent: AgentType) -> list[str]:
    mcp_tools = getattr(agent, "mcp_tools", None)
    if not mcp_tools:
        return []
    if isinstance(mcp_tools, dict):
        if mcp_tools.get("name"):
            return [str(mcp_tools["name"])]
        return [str(name) for name in mcp_tools]

    names: list[str] = []
    for tool in mcp_tools:
        if isinstance(tool, dict):
            name = tool.get("name")
        else:
            name = getattr(tool, "name", None)
        if name:
            names.append(str(name))
    if names:
        return names

    try:
        return [str(index) for index in range(len(mcp_tools))]
    except TypeError:
        return [str(mcp_tools)]


def _session_connected(session: Any) -> bool:
    if session is None:
        return False
    if isinstance(session, dict):
        return bool(session.get("connected", False))
    return bool(getattr(session, "connected", False))
