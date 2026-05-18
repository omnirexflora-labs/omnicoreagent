from __future__ import annotations

from collections.abc import Callable
from typing import Any

from omnicoreagent.core.events.base import Event
from omnicoreagent.core.types import (
    ParsedResponse,
    SessionState,
    ToolCallResult,
    ToolError,
)
from omnicoreagent.core.agents.display import show_tool_response


class AgentToolActionRunner:
    def __init__(
        self,
        *,
        agent_name: str,
        tool_call_resolver: Any,
        tool_failure_handler: Any,
        tool_batch_runner: Any,
        tool_observation_handler: Any,
    ):
        self.agent_name = agent_name
        self.tool_call_resolver = tool_call_resolver
        self.tool_failure_handler = tool_failure_handler
        self.tool_batch_runner = tool_batch_runner
        self.tool_observation_handler = tool_observation_handler

    async def resolve_tool_call_request(
        self,
        *,
        parsed_response: ParsedResponse,
        sessions: dict | None,
        mcp_tools: dict | None,
        local_tools: Any = None,
        sub_agents: list | None = None,
    ) -> ToolError | list[ToolCallResult]:
        return await self.tool_call_resolver.resolve(
            parsed_response=parsed_response,
            sessions=sessions,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

    async def run(
        self,
        *,
        parsed_response: ParsedResponse,
        response: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        system_prompt: str,
        reset_system_prompt: Callable[..., Any],
        debug: bool = False,
        sessions: dict | None = None,
        mcp_tools: dict | None = None,
        local_tools: Any = None,
        session_id: str | None = None,
        event_router: Callable[[str | None, Event], Any] | None = None,
        telemetry_recorder: Any = None,
        sub_agents: list | None = None,
    ):
        tool_call_result = await self.resolve_tool_call_request(
            parsed_response=parsed_response,
            mcp_tools=mcp_tools,
            sessions=sessions,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

        (
            tool_batch_name,
            tool_batch_args,
            obs_text,
            tools_results,
        ) = await self._execute_or_handle_error(
            tool_call_result=tool_call_result,
            response=response,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            session_id=session_id,
            event_router=event_router,
            telemetry_recorder=telemetry_recorder,
        )

        if debug:
            show_tool_response(
                agent_name=self.agent_name,
                tool_name=tool_batch_name,
                tool_args=tool_batch_args,
                observation=obs_text,
            )

        await self.tool_observation_handler.append_observations(
            tools_results=tools_results,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            session_id=session_id,
            debug=debug,
        )

        await self.tool_failure_handler.handle_loop_state(
            tool_call_results=self._as_tool_call_results(tool_call_result),
            session_state=session_state,
            system_prompt=system_prompt,
            session_id=session_id,
            event_router=event_router,
            debug=debug,
            reset_system_prompt=reset_system_prompt,
        )

    async def _execute_or_handle_error(
        self,
        *,
        tool_call_result: ToolError | list[ToolCallResult],
        response: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        event_router: Callable[[str | None, Event], Any] | None,
        telemetry_recorder: Any = None,
    ) -> tuple[str, list[dict[str, Any]], str | None, list[dict[str, Any]]]:
        if isinstance(tool_call_result, ToolError):
            return await self.tool_failure_handler.handle_validation_error(
                tool_error=tool_call_result,
                session_state=session_state,
                session_id=session_id,
                event_router=event_router,
            )

        tool_call_results = list(tool_call_result)
        tool_batch_name, tool_batch_args = await self.tool_batch_runner.start(
            tool_call_results=tool_call_results,
            response=response,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            session_id=session_id,
            event_router=event_router,
            telemetry_recorder=telemetry_recorder,
        )
        obs_text, tools_results = await self.tool_batch_runner.execute(
            tool_call_results=tool_call_results,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            session_id=session_id,
            event_router=event_router,
            telemetry_recorder=telemetry_recorder,
            tool_batch_name=tool_batch_name,
            tool_batch_args=tool_batch_args,
            parse_tool_observation=self.tool_observation_handler.parse,
            build_tool_results_observation=(
                self.tool_observation_handler.build_results_observation
            ),
        )
        return tool_batch_name, tool_batch_args, obs_text, tools_results

    def _as_tool_call_results(
        self, tool_call_result: ToolError | list[ToolCallResult]
    ) -> list[Any]:
        if isinstance(tool_call_result, (list, tuple)):
            return list(tool_call_result)
        return [tool_call_result]
