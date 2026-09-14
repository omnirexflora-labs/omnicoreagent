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
        parent_context = (
            telemetry_recorder.current_context()
            if telemetry_recorder is not None
            else None
        )
        spawn_event_id = None
        terminal_event_id = None
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
                spawn_event = await telemetry_recorder.emit_event(
                    "subagent_spawn",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "parameters": call.get("parameters", {}),
                    },
                    metadata={
                        "subagent_span_id": span.span_id,
                        "parent_trace_id": (
                            parent_context.trace_id if parent_context else None
                        ),
                        "parent_span_id": (
                            parent_context.span_id if parent_context else None
                        ),
                    },
                )
                spawn_event_id = spawn_event.event_id
            agent = resolve_agent(agent_name, sub_agents)
            if telemetry_recorder is not None:
                inherit_telemetry = getattr(agent, "_inherit_telemetry", None)
                if callable(inherit_telemetry):
                    inherit_telemetry(telemetry_recorder)
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
            succeeded = (
                not isinstance(result, dict)
                or result.get("status", "success") == "success"
            )
            if telemetry_recorder is not None:
                child_trace_id = (
                    result.get("trace_id") if isinstance(result, dict) else None
                )
                child_run_id = (
                    result.get("run_id") if isinstance(result, dict) else None
                )
                terminal_event = await telemetry_recorder.emit_event(
                    "subagent_result" if succeeded else "subagent_error",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    output={"result": result},
                    metadata={
                        "subagent_span_id": span.span_id if span else None,
                        "spawn_event_id": spawn_event_id,
                        "child_trace_id": child_trace_id,
                        "child_run_id": child_run_id,
                    },
                )
                terminal_event_id = terminal_event.event_id
            if telemetry_recorder is not None and span is not None:
                child_trace_id = (
                    result.get("trace_id") if isinstance(result, dict) else None
                )
                child_run_id = (
                    result.get("run_id") if isinstance(result, dict) else None
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.OK if succeeded else SpanStatus.ERROR,
                    output={
                        "agent_name": agent_name,
                        "status": "success" if succeeded else "error",
                        "child_trace_id": child_trace_id,
                        "child_run_id": child_run_id,
                        "spawn_event_id": spawn_event_id,
                        "terminal_event_id": terminal_event_id,
                    },
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
                terminal_event = await telemetry_recorder.emit_event(
                    "subagent_error",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    error={"type": e.__class__.__name__, "message": "cancelled"},
                    metadata={
                        "subagent_span_id": span.span_id if span else None,
                        "spawn_event_id": spawn_event_id,
                    },
                )
                terminal_event_id = terminal_event.event_id
            if telemetry_recorder is not None and span is not None:
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.CANCELLED,
                    output={
                        "spawn_event_id": spawn_event_id,
                        "terminal_event_id": terminal_event_id,
                    },
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
                terminal_event = await telemetry_recorder.emit_event(
                    "subagent_error",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={"session_id": session_id, "agent_name": agent_name},
                    error={"type": e.__class__.__name__, "message": str(e)},
                    metadata={
                        "subagent_span_id": span.span_id if span else None,
                        "spawn_event_id": spawn_event_id,
                    },
                )
                terminal_event_id = terminal_event.event_id
            if telemetry_recorder is not None and span is not None:
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.ERROR,
                    output={
                        "spawn_event_id": spawn_event_id,
                        "terminal_event_id": terminal_event_id,
                    },
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
