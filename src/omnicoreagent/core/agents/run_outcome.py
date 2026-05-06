from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from omnicoreagent.core.agents import events as agent_events
from omnicoreagent.core.events.base import Event
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.types import AgentState, Message, SessionState


class AgentRunOutcomeHandler:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def handle_final_answer(
        self,
        *,
        answer: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str,
        event_router: Callable[[str, Event], Any] | None,
        run_usage: Usage,
        start_time: float,
    ) -> dict[str, Any]:
        session_state.messages.append(Message(role="assistant", content=answer))
        await agent_events.emit_final_answer(
            event_router=event_router,
            session_id=session_id,
            agent_name=self.agent_name,
            message=str(answer),
        )
        await add_message_to_history(
            role="assistant",
            content=answer,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )

        session_state.state = AgentState.FINISHED
        run_usage.total_time = time.perf_counter() - start_time
        return {"answer": answer, "usage": run_usage}

    def max_steps_result(
        self,
        *,
        max_steps: int,
        last_valid_response: str | None,
        run_usage: Usage,
    ) -> dict[str, Any]:
        if last_valid_response:
            context = (
                "[SYSTEM_CONTEXT: MAX_STEPS_REACHED - "
                f"Agent hit {max_steps} step limit]\n\n"
            )
            return {"answer": context + last_valid_response, "usage": run_usage}

        return {
            "answer": (
                "[SYSTEM_CONTEXT: MAX_STEPS_REACHED - "
                f"Agent hit {max_steps} step limit without valid response]"
            ),
            "usage": run_usage,
        }

    def loop_stuck_result(self, last_valid_response: str | None) -> str | None:
        if not last_valid_response:
            return None

        return (
            "[SYSTEM_CONTEXT: LOOP_DETECTED - Agent stuck in tool call loop]\n\n"
            + last_valid_response
        )
