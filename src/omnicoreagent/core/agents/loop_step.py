from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.types import AgentState, Message, ParsedResponse, SessionState
from omnicoreagent.core.logging import logger


@dataclass
class AgentLoopStepResult:
    run_result: Any = None
    last_valid_response: str | None = None

    @property
    def should_return(self) -> bool:
        return self.run_result is not None


class AgentLoopStepHandler:
    def __init__(
        self,
        *,
        agent_name: str,
        max_steps: int,
        run_outcome_handler: Any,
        tool_action_runner: Any,
        subagent_runner: Any,
        reset_system_prompt: Callable[..., Any],
    ):
        self.agent_name = agent_name
        self.max_steps = max_steps
        self.run_outcome_handler = run_outcome_handler
        self.tool_action_runner = tool_action_runner
        self.subagent_runner = subagent_runner
        self.reset_system_prompt = reset_system_prompt

    async def handle(
        self,
        *,
        parsed_response: ParsedResponse,
        response: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        system_prompt: str,
        session_id: str,
        run_usage: Usage,
        start_time: float,
        current_steps: int,
        last_valid_response: str | None,
        debug: bool = False,
        sessions: dict | None = None,
        mcp_tools: dict | None = None,
        local_tools: Any = None,
        telemetry_recorder: Any = None,
        sub_agents: list | None = None,
    ) -> AgentLoopStepResult:
        if debug:
            logger.info(f"current steps: {current_steps}")

        if parsed_response.answer is not None:
            last_valid_response = parsed_response.answer
            return AgentLoopStepResult(
                run_result=await self.run_outcome_handler.handle_final_answer(
                    answer=parsed_response.answer,
                    session_state=session_state,
                    add_message_to_history=add_message_to_history,
                    session_id=session_id,
                    run_usage=run_usage,
                    start_time=start_time,
                ),
                last_valid_response=last_valid_response,
            )

        if parsed_response.action is not None:
            if parsed_response.agent_calls is not None:
                await self.subagent_runner.execute(
                    response=response,
                    agent_calls=parsed_response.data,
                    sub_agents=sub_agents,
                    session_id=session_id,
                    session_state=session_state,
                    add_message_to_history=add_message_to_history,
                    run_usage=run_usage,
                    telemetry_recorder=telemetry_recorder,
                    debug=debug,
                )
            else:
                await self.tool_action_runner.run(
                    parsed_response=parsed_response,
                    response=response,
                    session_state=session_state,
                    add_message_to_history=add_message_to_history,
                    system_prompt=system_prompt,
                    reset_system_prompt=self.reset_system_prompt,
                    debug=debug,
                    sessions=sessions,
                    mcp_tools=mcp_tools,
                    local_tools=local_tools,
                    session_id=session_id,
                    telemetry_recorder=telemetry_recorder,
                    sub_agents=sub_agents,
                )

        if parsed_response.error is not None:
            session_state.messages.append(
                Message(role="user", content=parsed_response.error)
            )
            return AgentLoopStepResult(last_valid_response=last_valid_response)

        if current_steps >= self.max_steps:
            session_state.state = AgentState.STUCK
            return AgentLoopStepResult(
                run_result=self.run_outcome_handler.max_steps_result(
                    max_steps=self.max_steps,
                    last_valid_response=last_valid_response,
                    run_usage=run_usage,
                ),
                last_valid_response=last_valid_response,
            )

        return AgentLoopStepResult(last_valid_response=last_valid_response)
