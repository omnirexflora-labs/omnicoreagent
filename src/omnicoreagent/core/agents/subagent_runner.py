from __future__ import annotations

import asyncio
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.agents.subagent_helpers import (
    build_kwargs,
    resolve_agent,
)
from omnicoreagent.core.logging import logger


class SubAgentCallRunner:
    """Execute one configured child with cleanup and telemetry."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def run(
        self,
        call: dict[str, Any],
        sub_agents: list,
        session_id: str,
        telemetry_recorder: Any = None,
    ) -> tuple[str, Any]:
        agent_name = call.get("agent")
        if not agent_name:
            raise ValueError("agent_call missing 'agent' field")

        span = None
        agent = None
        cleanup_attempted = False
        try:
            if telemetry_recorder is not None:
                span = await telemetry_recorder.start_span(
                    name=f"subagent:{agent_name}",
                    kind="subagent.run",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "parameters": call.get("parameters", {}),
                    },
                )
                await telemetry_recorder.emit_event(
                    "subagent_spawn",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "parameters": call.get("parameters", {}),
                    },
                )
            agent = resolve_agent(agent_name, sub_agents)
            params = dict(call.get("parameters", {}))
            params["session_id"] = session_id
            kwargs = build_kwargs(agent, params)

            if hasattr(agent, "mcp_tools") and agent.mcp_tools:
                logger.info(f"Connecting MCP servers for {agent_name}...")
                await agent.connect_mcp_servers()

            logger.info(f"Running sub-agent: {agent_name}")
            result = await agent.run(**kwargs)
            cleanup_attempted = True
            await self._cleanup_agent(agent_name, agent)
            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "subagent_result",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    output={"result": result},
                )
            if telemetry_recorder is not None and span is not None:
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.OK,
                    output={"agent_name": agent_name},
                )
            return agent_name, result

        except asyncio.CancelledError as e:
            logger.error(f"Sub-agent {agent_name} execution was cancelled")
            if agent is not None and not cleanup_attempted:
                try:
                    await self._cleanup_agent(agent_name, agent)
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to cleanup cancelled sub-agent {agent_name}: "
                        f"{cleanup_error}"
                    )
            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "subagent_error",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    error={"type": e.__class__.__name__, "message": "cancelled"},
                )
            if telemetry_recorder is not None and span is not None:
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.CANCELLED,
                    error={"type": e.__class__.__name__, "message": "cancelled"},
                )
            raise

        except Exception as e:
            logger.error(f"Error executing agent {agent_name}: {e}", exc_info=True)
            if agent is not None and not cleanup_attempted:
                try:
                    await self._cleanup_agent(agent_name, agent)
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to cleanup sub-agent {agent_name} after error: "
                        f"{cleanup_error}"
                    )
            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "subagent_error",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    error={"type": e.__class__.__name__, "message": str(e)},
                )
            if telemetry_recorder is not None and span is not None:
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.ERROR,
                    error={"type": e.__class__.__name__, "message": str(e)},
                )
            return agent_name, e

    async def _cleanup_agent(self, agent_name: str, agent: Any) -> None:
        cleanup = getattr(agent, "cleanup_mcp_servers", None)
        if not callable(cleanup):
            return
        try:
            await cleanup()
        except Exception as exc:
            logger.error(f"Failed to cleanup sub-agent {agent_name}: {exc}")
            raise
