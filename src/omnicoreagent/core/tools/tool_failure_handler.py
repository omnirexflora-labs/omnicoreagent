from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.core.events.base import (
    Event,
    EventType,
    ToolCallErrorPayload,
)
from omnicoreagent.core.types import AgentState, Message, SessionState, ToolError
from omnicoreagent.core.utils import handle_stuck_state, logger


class ToolFailureHandler:
    """Handle failed tool resolution and repeated tool-call loops."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    def build_validation_error_results(
        self,
        tool_errors: list[ToolError],
        fallback_message: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "tool_name": getattr(single_tool, "tool_name", "unknown"),
                "args": getattr(single_tool, "tool_args", {}),
                "status": "error",
                "data": None,
                "message": getattr(single_tool, "observation", fallback_message),
            }
            for single_tool in tool_errors
        ]

    async def handle_validation_error(
        self,
        tool_error: ToolError,
        session_state: SessionState,
        session_id: str | None,
        event_router: Callable[[str | None, Event], Awaitable[Any]] | None,
    ) -> tuple[str, list[dict[str, Any]], str, list[dict[str, Any]]]:
        obs_text = tool_error.observation
        tool_errors = [tool_error]

        for single_tool in tool_errors:
            tool_name = getattr(single_tool, "tool_name", "unknown")
            tool_args = getattr(single_tool, "tool_args", {})
            error_message = getattr(single_tool, "observation", obs_text)

            event = Event(
                type=EventType.TOOL_CALL_ERROR,
                payload=ToolCallErrorPayload(
                    tool_name=tool_name,
                    error_message=error_message,
                ),
                agent_name=self.agent_name,
            )

            if event_router:
                await event_router(session_id=session_id, event=event)
            session_state.loop_detector.record_tool_call(
                str(tool_name),
                str(tool_args),
                str(error_message),
            )

        tool_batch_name = ", ".join(
            [getattr(tool, "tool_name", "unknown") for tool in tool_errors]
        )
        tool_batch_args = [getattr(tool, "tool_args", {}) for tool in tool_errors]

        logger.error(
            f"Tool call validation failed for: {tool_batch_name} "
            f"args={tool_batch_args} -> {obs_text}"
        )

        return (
            tool_batch_name,
            tool_batch_args,
            obs_text,
            self.build_validation_error_results(
                tool_errors=tool_errors,
                fallback_message=obs_text,
            ),
        )

    async def handle_loop_state(
        self,
        tool_call_results: list[Any],
        session_state: SessionState,
        system_prompt: str,
        session_id: str | None,
        event_router: Callable[[str | None, Event], Awaitable[Any]] | None,
        debug: bool,
        reset_system_prompt: Callable[..., Awaitable[list[Message]]],
    ) -> None:
        for single_tool in tool_call_results:
            tool_name = getattr(single_tool, "tool_name", None)
            if not tool_name:
                if isinstance(single_tool, (list, tuple)) and len(single_tool) >= 1:
                    tool_name = single_tool[0]
                else:
                    logger.warning(
                        "Skipping malformed tool_call_result item: %s", single_tool
                    )
                    continue

            if not session_state.loop_detector.is_looping(tool_name):
                continue

            loop_type = session_state.loop_detector.get_loop_type(tool_name)
            logger.warning(f"Tool call loop detected for '{tool_name}': {loop_type}")

            new_system_prompt = handle_stuck_state(system_prompt)
            session_state.messages = await reset_system_prompt(
                messages=session_state.messages,
                system_prompt=new_system_prompt,
            )

            loop_message = (
                f"Observation:\n"
                f"⚠️ Tool call loop detected for '{tool_name}': {loop_type}\n\n"
                "Current approach is not working. You MUST now provide a final answer to the user.\n"
                "Please:\n"
                "1. Stop trying the same approach\n"
                "2. Provide your best response to the user based on what you know\n"
                "3. Use <final_answer>Your response here</final_answer> format\n"
                "4. Be helpful and explain any limitations if needed\n"
                "5. Do NOT continue with more tool calls\n"
                "\nYou MUST respond with <final_answer> tags now.\n"
            )

            event = Event(
                type=EventType.TOOL_CALL_ERROR,
                payload=ToolCallErrorPayload(
                    tool_name=tool_name,
                    error_message=loop_message,
                ),
                agent_name=self.agent_name,
            )
            if event_router:
                await event_router(session_id=session_id, event=event)

            session_state.messages.append(Message(role="user", content=loop_message))

            if debug:
                logger.info(
                    f"Agent state changed from {session_state.state} to {AgentState.STUCK}"
                )

            session_state.state = AgentState.STUCK
            session_state.loop_detector.reset(tool_name)
