from __future__ import annotations

from contextlib import asynccontextmanager

from omnicoreagent.core.agents.loop_detection import NativeLoopDetector
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
                loop_detector=NativeLoopDetector(),
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
            if previous_state in (AgentState.IDLE, AgentState.ERROR):
                self._release(session_id, session_state)

    def _release(self, session_id: str, session_state: SessionState) -> None:
        """Forget a session whose run has ended, on every way a run can end.

        Nothing in the state outlives a run: ``reset_for_run`` empties it
        before the next one. But the store kept one per session for the life
        of the process, with the last run's messages and its loop detector.
        In the 30-minute server soak (2026-10-07) that was about 37 KB a visit
        with no plateau, and the first thing to grow in the support desk's
        heap. A run that ends by completing, failing, being cancelled or
        suspending all leave through this ``finally``.
        """
        key = (session_id, self.agent_name)
        if self.states.get(key) is session_state:
            del self.states[key]
        session_state.messages = []
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        session_state.observation_event_ids = {}
        session_state.delivered_observation_event_ids = set()
