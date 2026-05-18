from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.core.logging import logger
from omnicoreagent.core.telemetry import ActorType, TelemetryActor
from omnicoreagent.core.types import AgentState, Message, SessionState, ToolError


def build_stuck_state_prompt(message_stuck_prompt: bool = False) -> str:
    """Build deterministic stuck-loop guidance for the agent prompt."""
    if message_stuck_prompt:
        return (
            "You are stuck in a loop. This must be addressed immediately.\n\n"
            "REQUIRED ACTIONS:\n"
            "1. STOP the current approach\n"
            "2. ANALYZE why the previous attempts failed\n"
            "3. TRY a completely different method\n"
            "4. IF the issue cannot be resolved:\n"
            "   - Explain clearly why not\n"
            "   - Provide alternative solutions\n"
            "   - DO NOT repeat the same failed action\n\n"
            "   - DO NOT try again. immediately stop and do not try again.\n\n"
            "   - Tell user your last known good state, error message and the current state of the conversation.\n\n"
            "CONTINUING THE SAME APPROACH WILL RESULT IN FURTHER FAILURES"
        )

    return (
        "It looks like you're stuck or repeating an ineffective approach.\n"
        "Take a moment to do the following:\n"
        "1. Reflect: Analyze why the previous step didn't work (e.g., tool call failure, irrelevant observation).\n"
        "2. Try Again Differently: Use a different tool, change the inputs, or attempt a new strategy.\n"
        "3. If Still Unsolvable:\n"
        "   - Clearly explain to the user why the issue cannot be solved.\n"
        "   - Provide any relevant reasoning or constraints.\n"
        "   - Offer one or more alternative solutions or next steps.\n"
        "   - DO NOT try again. immediately stop and do not try again.\n\n"
        "   - Tell user your last known good state, error message and the current state of the conversation.\n\n"
        "Do not repeat the same failed strategy or go silent."
    )


def handle_stuck_state(
    _original_system_prompt: str, message_stuck_prompt: bool = False
) -> str:
    return (
        f"{build_stuck_state_prompt(message_stuck_prompt)}\n\n"
        "Your previous approaches to solve this problem have failed. "
        "You need to try something completely different.\n\n"
    )


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
        telemetry_recorder: Any = None,
    ) -> tuple[str, list[dict[str, Any]], str, list[dict[str, Any]]]:
        obs_text = tool_error.observation
        tool_errors = [tool_error]

        for single_tool in tool_errors:
            tool_name = getattr(single_tool, "tool_name", "unknown")
            tool_args = getattr(single_tool, "tool_args", {})
            error_message = getattr(single_tool, "observation", obs_text)

            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "tool_error",
                    actor=TelemetryActor(type=ActorType.TOOL, name=str(tool_name)),
                    input={"tool_name": tool_name, "tool_args": tool_args},
                    error={"type": "ToolValidationError", "message": error_message},
                )
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
        debug: bool,
        reset_system_prompt: Callable[..., Awaitable[list[Message]]],
        telemetry_recorder: Any = None,
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

            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "tool_error",
                    actor=TelemetryActor(type=ActorType.TOOL, name=str(tool_name)),
                    input={"tool_name": tool_name},
                    error={"type": "ToolLoopDetected", "message": loop_message},
                )

            session_state.messages.append(Message(role="user", content=loop_message))

            if debug:
                logger.info(
                    f"Agent state changed from {session_state.state} to {AgentState.STUCK}"
                )

            session_state.state = AgentState.STUCK
            session_state.loop_detector.reset(tool_name)
