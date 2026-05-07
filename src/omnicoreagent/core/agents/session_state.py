from __future__ import annotations

from contextlib import asynccontextmanager

from omnicoreagent.core.agents.loop_detection import RobustLoopDetector
from omnicoreagent.core.logging import logger
from omnicoreagent.core.types import AgentState, SessionState


class AgentSessionStateStore:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.states: dict[tuple[str, str], SessionState] = {}

    def get(self, session_id: str, debug: bool) -> SessionState:
        key = (session_id, self.agent_name)
        if key not in self.states:
            self.states[key] = SessionState(
                messages=[],
                state=AgentState.IDLE,
                loop_detector=RobustLoopDetector(debug=debug),
                assistant_with_tool_calls=None,
                pending_tool_responses=[],
            )
        return self.states[key]

    def reset_for_run(self, session_id: str, debug: bool) -> SessionState:
        session_state = self.get(session_id=session_id, debug=debug)
        session_state.messages = []
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        session_state.loop_detector.reset()
        return session_state

    @asynccontextmanager
    async def state_context(
        self, *, new_state: AgentState, session_id: str, debug: bool
    ):
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid agent state: {new_state}")

        session_state = self.get(session_id=session_id, debug=debug)
        previous_state = session_state.state
        session_state.state = new_state
        try:
            yield
        except Exception as e:
            session_state.state = AgentState.ERROR
            logger.error(f"Error in agent state context: {e}")
            raise
        finally:
            session_state.state = previous_state
