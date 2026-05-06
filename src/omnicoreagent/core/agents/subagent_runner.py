from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from omnicoreagent.core.events.base import (
    Event,
    EventType,
    SubAgentCallErrorPayload,
    SubAgentCallResultPayload,
    SubAgentCallStartedPayload,
)
from omnicoreagent.core.token_usage import Usage, usage
from omnicoreagent.core.types import Message
from omnicoreagent.core.utils import (
    build_kwargs,
    build_sub_agents_observation_xml,
    logger,
    resolve_agent,
    show_sub_agent_call_result,
)


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
        event_router: Callable[..., Any] | None = None,
        debug: bool = False,
    ):
        agent_calls = self._normalize_agent_calls(agent_calls)
        await self._emit_started(
            agent_calls=agent_calls,
            session_id=session_id,
            event_router=event_router,
        )
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
                self._execute_single_agent(call, sub_agents, session_id)
                for call in agent_calls
            ],
            return_exceptions=True,
        )

        observations = await self._collect_observations(
            results=results,
            run_usage=run_usage,
            session_id=session_id,
            event_router=event_router,
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

    async def _emit_started(
        self,
        agent_calls: list[dict[str, Any]],
        session_id: str,
        event_router: Callable[..., Any] | None,
    ):
        if not event_router:
            return
        event = Event(
            type=EventType.SUB_AGENT_CALL_STARTED,
            payload=SubAgentCallStartedPayload(
                agent_name=self.agent_name,
                session_id=session_id,
                timestamp=str(datetime.now()),
                run_count=0,
                kwargs={"agent_calls": agent_calls},
            ),
            agent_name=self.agent_name,
        )
        await event_router(session_id=session_id, event=event)

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
    ) -> tuple[str, Any]:
        agent_name = call.get("agent")
        if not agent_name:
            raise ValueError("agent_call missing 'agent' field")

        try:
            agent = resolve_agent(agent_name, sub_agents)
            params = call.get("parameters", {})
            params["session_id"] = session_id
            kwargs = build_kwargs(agent, params)

            if hasattr(agent, "mcp_tools") and agent.mcp_tools:
                logger.info(f"Connecting MCP servers for {agent_name}...")
                await agent.connect_mcp_servers()

            logger.info(f"Running sub-agent: {agent_name}")
            result = await agent.run(**kwargs)
            await agent.cleanup_mcp_servers()
            return agent_name, result

        except Exception as e:
            logger.error(f"Error executing agent {agent_name}: {e}", exc_info=True)
            return agent_name, e

    async def _collect_observations(
        self,
        results: list[Any],
        run_usage: Usage,
        session_id: str,
        event_router: Callable[..., Any] | None,
    ) -> list[dict[str, Any]]:
        observations = []
        for result in results:
            if isinstance(result, Exception):
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
                        event_router=event_router,
                    )
                )
                continue

            observations.append(
                await self._handle_agent_success(
                    agent_name=agent_name,
                    result=obs_data,
                    session_id=session_id,
                    run_usage=run_usage,
                    event_router=event_router,
                )
            )
        return observations

    async def _handle_agent_error(
        self,
        agent_name: str,
        error: Exception,
        session_id: str,
        event_router: Callable[..., Any] | None,
    ) -> dict[str, Any]:
        logger.error(f"Agent {agent_name} execution failed: {error}")
        if event_router:
            event = Event(
                type=EventType.SUB_AGENT_CALL_ERROR,
                payload=SubAgentCallErrorPayload(
                    agent_name=agent_name,
                    session_id=session_id,
                    timestamp=str(datetime.now()),
                    error=str(error),
                    error_count=0,
                ),
                agent_name=self.agent_name,
            )
            await event_router(session_id=session_id, event=event)
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
        event_router: Callable[..., Any] | None,
    ) -> dict[str, Any]:
        output = self._extract_agent_output(result)
        logger.info(f"Agent {agent_name} completed successfully")
        if isinstance(result, dict):
            sub_usage = result.get("metric")
            if sub_usage and isinstance(sub_usage, Usage):
                run_usage.incr(sub_usage)
                usage.incr(sub_usage)

        if event_router:
            event = Event(
                type=EventType.SUB_AGENT_CALL_RESULT,
                payload=SubAgentCallResultPayload(
                    agent_name=agent_name,
                    session_id=session_id,
                    timestamp=str(datetime.now()),
                    run_count=0,
                    result=result,
                ),
                agent_name=self.agent_name,
            )
            await event_router(session_id=session_id, event=event)

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
