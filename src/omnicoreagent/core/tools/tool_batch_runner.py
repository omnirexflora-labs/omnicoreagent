import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.core.events.base import Event
from omnicoreagent.core.tools.tool_batch_events import (
    assign_tool_call_ids,
    build_tool_batch_args,
    build_tool_batch_name,
    build_tool_call_error_event,
    build_tool_call_history_metadata,
    build_tool_call_result_event,
    build_tool_call_started_event,
)
from omnicoreagent.core.types import (
    Message,
    SessionState,
    ToolCallResult,
)
from omnicoreagent.core.logging import logger

TOOL_CALL_TIMEOUT_MESSAGE = (
    "Tool call timed out. Please try again or use a different approach."
)


class ToolBatchRunner:
    """Run resolved tool calls as a single parallel batch."""

    def __init__(self, agent_name: str, tool_call_timeout: int):
        self.agent_name = agent_name
        self.tool_call_timeout = tool_call_timeout

    def build_error_results(
        self,
        tool_call_results: list[ToolCallResult],
        error_message: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "tool_name": getattr(single_tool, "tool_name", "unknown"),
                "args": getattr(single_tool, "tool_args", {}),
                "status": "error",
                "data": None,
                "message": error_message,
            }
            for single_tool in tool_call_results
        ]

    async def handle_execution_error(
        self,
        tool_call_results: list[ToolCallResult],
        error_message: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        event_router: Callable[[str, Event], Any] | None,
        tool_batch_name: str,
    ) -> list[dict[str, Any]]:
        for single_tool in tool_call_results:
            session_state.loop_detector.record_tool_call(
                str(single_tool.tool_name),
                str(single_tool.tool_args),
                error_message,
            )

        for single_tool in tool_call_results:
            await add_message_to_history(
                role="tool",
                content=error_message,
                metadata={
                    "tool_call_id": single_tool.tool_call_id,
                    "tool": single_tool.tool_name,
                    "args": single_tool.tool_args,
                    "agent_name": self.agent_name,
                },
                session_id=session_id,
            )

        event = build_tool_call_error_event(
            agent_name=self.agent_name,
            tool_batch_name=tool_batch_name,
            error_message=error_message,
        )
        if event_router:
            await event_router(session_id=session_id, event=event)

        return self.build_error_results(
            tool_call_results=tool_call_results,
            error_message=error_message,
        )

    async def start(
        self,
        tool_call_results: list[ToolCallResult],
        response: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        event_router: Callable[[str, Event], Any] | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        tool_batch_name = build_tool_batch_name(tool_call_results)
        tool_batch_args = build_tool_batch_args(tool_call_results)
        assign_tool_call_ids(tool_call_results)
        tool_calls_metadata = build_tool_call_history_metadata(
            agent_name=self.agent_name,
            tool_call_results=tool_call_results,
        )

        event = build_tool_call_started_event(
            agent_name=self.agent_name,
            tool_batch_name=tool_batch_name,
            tool_batch_args=tool_batch_args,
            first_tool_call_id=tool_call_results[0].tool_call_id,
        )
        if event_router:
            await event_router(session_id=session_id, event=event)

        await add_message_to_history(
            role="assistant",
            content=response,
            metadata=tool_calls_metadata.model_dump(),
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=response))

        return tool_batch_name, tool_batch_args

    async def execute(
        self,
        tool_call_results: list[ToolCallResult],
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        event_router: Callable[[str, Event], Any] | None,
        tool_batch_name: str,
        tool_batch_args: list[dict[str, Any]],
        parse_tool_observation: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        build_tool_results_observation: Callable[
            [list[ToolCallResult], list[dict[str, Any]], SessionState, str | None],
            str,
        ],
    ) -> tuple[str, list[dict[str, Any]]]:
        try:
            async with asyncio.timeout(self.tool_call_timeout):
                tool_outputs = await asyncio.gather(
                    *[
                        single_tool.tool_executor.execute(
                            agent_name=self.agent_name,
                            tool_args=single_tool.tool_args,
                            tool_name=single_tool.tool_name,
                            tool_call_id=single_tool.tool_call_id,
                            add_message_to_history=add_message_to_history,
                            session_id=session_id,
                        )
                        for single_tool in tool_call_results
                    ]
                )

            observation = await parse_tool_observation(
                {
                    "status": (
                        "error"
                        if any(
                            result.get("status") == "error" for result in tool_outputs
                        )
                        else "success"
                    ),
                    "tools_results": tool_outputs,
                }
            )

            tools_results = observation.get("tools_results", [])
            obs_text = build_tool_results_observation(
                tool_call_results,
                tools_results,
                session_state,
                session_id,
            )

            event = build_tool_call_result_event(
                agent_name=self.agent_name,
                tool_batch_name=tool_batch_name,
                tool_batch_args=tool_batch_args,
                result=obs_text,
                first_tool_call_id=tool_call_results[0].tool_call_id,
            )
            if event_router:
                await event_router(session_id=session_id, event=event)

            return obs_text, tools_results

        except asyncio.TimeoutError:
            obs_text = TOOL_CALL_TIMEOUT_MESSAGE
            logger.warning(obs_text)
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                event_router=event_router,
                tool_batch_name=tool_batch_name,
            )
            return obs_text, tools_results

        except Exception as e:
            obs_text = f"Error executing tool: {str(e)}"
            logger.error(obs_text)
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                event_router=event_router,
                tool_batch_name=tool_batch_name,
            )
            return obs_text, tools_results
