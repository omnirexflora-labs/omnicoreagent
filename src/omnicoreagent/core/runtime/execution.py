from __future__ import annotations

from collections.abc import Callable
from typing import Any

from omnicoreagent.core.telemetry import ActorType, TelemetryActor
from omnicoreagent.core.runtime.imports import runtime_logger


async def blocked_guardrail_response(
    *,
    guardrail: Any,
    query: str,
    session_id: str | None,
    agent_name: str,
    telemetry_recorder: Any = None,
) -> dict[str, Any] | None:
    """Return a blocked response when input guardrails reject the query."""
    if not guardrail:
        return None

    result = guardrail.check(query)
    if telemetry_recorder is not None:
        await telemetry_recorder.emit_event(
            "guardrail_check",
            actor=TelemetryActor(type=ActorType.GUARDRAIL),
            input={"target": "user_input", "agent_name": agent_name},
            output=result.to_dict() if hasattr(result, "to_dict") else {"safe": result.is_safe},
        )
    if result.is_safe:
        return None

    runtime_logger().warning(f"Query blocked by guardrail: {result.message}")
    return {
        "response": (
            "I'm sorry, but I cannot process this request due to safety concerns: "
            f"{result.message}"
        ),
        "session_id": session_id,
        "agent_name": agent_name,
        "guardrail_result": result.to_dict(),
    }


def build_agent_run_kwargs(
    *,
    mcp_client: Any,
    local_tools: Any,
    session_id: str,
    sub_agents: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the tool/session kwargs passed into the ReactAgent loop."""
    return {
        "sessions": mcp_client.sessions if mcp_client else {},
        "mcp_tools": mcp_client.available_tools if mcp_client else {},
        "local_tools": local_tools,
        "session_id": session_id,
        "sub_agents": sub_agents,
    }


def format_run_response(
    *,
    response: Any,
    session_id: str,
    agent_name: str,
    usage_getter: Callable[[], Any],
) -> dict[str, Any]:
    """Normalize ReactAgent output into the public OmniCoreAgent run response."""
    if isinstance(response, dict) and "usage" in response:
        usage = response["usage"]
        usage_getter().incr(usage)
        return {
            "response": response["answer"],
            "session_id": session_id,
            "agent_name": agent_name,
            "metric": usage,
        }

    return {"response": response, "session_id": session_id, "agent_name": agent_name}
