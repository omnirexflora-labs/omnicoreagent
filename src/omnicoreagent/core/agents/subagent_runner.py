from __future__ import annotations

import asyncio
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.agents.subagent_helpers import (
    accepts_run_id,
    build_kwargs,
    finish_delegation,
    new_child_run_id,
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
        redact_parameters: bool = False,
    ) -> tuple[str, Any]:
        agent_name = call.get("agent")
        if not agent_name:
            raise ValueError("agent_call missing 'agent' field")
        # Under governance delegation parameters are redacted in telemetry
        # like any other tool arguments; the child still receives them.
        recorded_parameters = (
            {key: "[REDACTED]" for key in call.get("parameters", {})}
            if redact_parameters
            else call.get("parameters", {})
        )

        span = None
        agent = None
        cleanup_attempted = False
        parent_context = (
            telemetry_recorder.current_context()
            if telemetry_recorder is not None
            else None
        )
        spawn_event_id = None
        child_run_id = None
        child_trace_id = None
        try:
            if telemetry_recorder is not None:
                span = await telemetry_recorder.start_span(
                    name=f"subagent:{agent_name}",
                    kind="subagent.run",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "parameters": recorded_parameters,
                    },
                )
            agent = resolve_agent(agent_name, sub_agents)
            # The child's run id is assigned here so the delegation stays
            # linked to the child trace on every terminal path.
            child_run_id = new_child_run_id() if accepts_run_id(agent) else None
            if telemetry_recorder is not None:
                spawn_event = await telemetry_recorder.emit_event(
                    "subagent_spawn",
                    actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
                    input={
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "parameters": recorded_parameters,
                    },
                    metadata={
                        "subagent_span_id": span.span_id,
                        "parent_trace_id": (
                            parent_context.trace_id if parent_context else None
                        ),
                        "parent_span_id": (
                            parent_context.span_id if parent_context else None
                        ),
                        "child_run_id": child_run_id,
                    },
                )
                spawn_event_id = spawn_event.event_id
                inherit_telemetry = getattr(agent, "_inherit_telemetry", None)
                if callable(inherit_telemetry):
                    inherit_telemetry(telemetry_recorder)
            params = dict(call.get("parameters", {}))
            params["session_id"] = session_id
            if child_run_id is not None:
                params["run_id"] = child_run_id
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
            if isinstance(result, dict):
                child_trace_id = result.get("trace_id")
                child_run_id = result.get("run_id") or child_run_id
            await finish_delegation(
                telemetry_recorder,
                span,
                agent_name=agent_name,
                session_id=session_id,
                spawn_event_id=spawn_event_id,
                parent_context=parent_context,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
                status=SpanStatus.OK if succeeded else SpanStatus.ERROR,
                output={"result": result},
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
            await finish_delegation(
                telemetry_recorder,
                span,
                agent_name=agent_name,
                session_id=session_id,
                spawn_event_id=spawn_event_id,
                parent_context=parent_context,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
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
            await finish_delegation(
                telemetry_recorder,
                span,
                agent_name=agent_name,
                session_id=session_id,
                spawn_event_id=spawn_event_id,
                parent_context=parent_context,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
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
