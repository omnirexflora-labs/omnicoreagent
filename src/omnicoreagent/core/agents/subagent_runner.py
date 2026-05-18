from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.token_usage import Usage, usage
from omnicoreagent.core.types import Message
from omnicoreagent.core.agents.display import show_sub_agent_call_result
from omnicoreagent.core.agents.subagent_helpers import (
    build_kwargs,
    build_sub_agents_observation_xml,
    resolve_agent,
)
from omnicoreagent.core.logging import logger


class SubAgentCallRunner:
    """Execute model-requested sub-agent calls and append observations."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def execute(
        self,
        response: str,
        agent_calls: list | str,
        sub_agents: list,
        session_id: str,
        session_state: Any,
        add_message_to_history: Callable[..., Any],
        run_usage: Usage,
        telemetry_recorder: Any = None,
        debug: bool = False,
    ):
        agent_calls = self._normalize_agent_calls(agent_calls)
        await self._record_assistant_call(
            response=response,
            agent_calls=agent_calls,
            session_id=session_id,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
        )

        logger.info(
            f"Executing {len(agent_calls)} sub-agents with concurrent MCP connections..."
        )
        results = await asyncio.gather(
            *[
                self._execute_single_agent(
                    call,
                    sub_agents,
                    session_id,
                    telemetry_recorder=telemetry_recorder,
                )
                for call in agent_calls
            ],
            return_exceptions=True,
        )

        observations = await self._collect_observations(
            results=results,
            run_usage=run_usage,
            session_id=session_id,
            telemetry_recorder=telemetry_recorder,
        )

        await self._append_observation_block(
            observations=observations,
            agent_calls=agent_calls,
            session_id=session_id,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            debug=debug,
        )

    def _normalize_agent_calls(self, agent_calls: list | str) -> list[dict[str, Any]]:
        if isinstance(agent_calls, str):
            return json.loads(agent_calls)
        return list(agent_calls)

    async def _record_assistant_call(
        self,
        response: str,
        agent_calls: list[dict[str, Any]],
        session_id: str,
        session_state: Any,
        add_message_to_history: Callable[..., Any],
    ):
        await add_message_to_history(
            role="assistant",
            content=response,
            metadata={"agent_calls": agent_calls},
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=response))

    async def _execute_single_agent(
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

    async def _collect_observations(
        self,
        results: list[Any],
        run_usage: Usage,
        session_id: str,
        telemetry_recorder: Any = None,
    ) -> list[dict[str, Any]]:
        observations = []
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                logger.error(f"Unexpected top-level exception: {result}")
                observations.append(
                    {
                        "agent_name": "unknown",
                        "status": "error",
                        "output": str(result),
                    }
                )
                continue

            agent_name, obs_data = result
            if isinstance(obs_data, Exception):
                observations.append(
                    await self._handle_agent_error(
                        agent_name=agent_name,
                        error=obs_data,
                        session_id=session_id,
                        telemetry_recorder=telemetry_recorder,
                    )
                )
                continue

            observations.append(
                await self._handle_agent_success(
                    agent_name=agent_name,
                    result=obs_data,
                    session_id=session_id,
                    run_usage=run_usage,
                    telemetry_recorder=telemetry_recorder,
                )
            )
        return observations

    async def _handle_agent_error(
        self,
        agent_name: str,
        error: Exception,
        session_id: str,
        telemetry_recorder: Any = None,
    ) -> dict[str, Any]:
        logger.error(f"Agent {agent_name} execution failed: {error}")
        return {
            "agent_name": agent_name,
            "status": "error",
            "output": str(error),
        }

    async def _handle_agent_success(
        self,
        agent_name: str,
        result: Any,
        session_id: str,
        run_usage: Usage,
        telemetry_recorder: Any = None,
    ) -> dict[str, Any]:
        output = self._extract_agent_output(result)
        logger.info(f"Agent {agent_name} completed successfully")
        if isinstance(result, dict):
            sub_usage = result.get("metric")
            if sub_usage and isinstance(sub_usage, Usage):
                run_usage.incr(sub_usage)
                usage.incr(sub_usage)

        return {
            "agent_name": agent_name,
            "status": "success",
            "output": output,
        }

    def _extract_agent_output(self, result: Any) -> str:
        if isinstance(result, dict):
            return result.get("response", result.get("output", str(result)))
        if isinstance(result, str):
            return result
        return str(result)

    async def _append_observation_block(
        self,
        observations: list[dict[str, Any]],
        agent_calls: list[dict[str, Any]],
        session_id: str,
        session_state: Any,
        add_message_to_history: Callable[..., Any],
        debug: bool,
    ):
        xml_obs_block = build_sub_agents_observation_xml(observations)
        agent_call_result = {
            "agent_name": self.agent_name,
            "agent_calls": agent_calls,
            "output": observations,
        }

        if debug:
            show_sub_agent_call_result(agent_call_result)

        session_state.messages.append(Message(role="user", content=xml_obs_block))
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name, "sub_agent_results": True},
        )
