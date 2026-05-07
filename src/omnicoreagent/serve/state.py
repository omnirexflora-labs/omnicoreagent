"""Typed app-state helpers for OmniServe."""

from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType

    from .config import OmniServeConfig
else:
    AgentType = Any
    OmniServeConfig = Any


def get_agent(request: Request) -> AgentType:
    """Return the agent bound to the FastAPI app."""
    return request.app.state.agent


def get_config(request: Request) -> OmniServeConfig:
    """Return the OmniServe config bound to the FastAPI app."""
    return request.app.state.config


def get_agent_name(agent: AgentType) -> str:
    """Return a stable display name for an agent-like object."""
    return getattr(agent, "name", "UnknownAgent")


def resolve_session_id(agent: AgentType, session_id: str | None) -> str:
    """Use a provided session id or ask the agent to generate one."""
    if session_id:
        return session_id
    return agent.generate_session_id()
