from __future__ import annotations

import pytest

from omnicoreagent.core.agents.session_state import AgentSessionStateStore
from omnicoreagent.core.types import AgentState, Message


def test_session_state_store_reuses_state_per_session_and_agent():
    store = AgentSessionStateStore(agent_name="agent")

    first = store.get(session_id="chat1", debug=False)
    second = store.get(session_id="chat1", debug=True)
    other = store.get(session_id="chat2", debug=False)

    assert first is second
    assert first is not other
    assert ("chat1", "agent") in store.states


def test_reset_for_run_clears_transient_loop_state():
    store = AgentSessionStateStore(agent_name="agent")
    state = store.get(session_id="chat1", debug=False)
    state.messages = [Message(role="user", content="hello")]
    state.assistant_with_tool_calls = Message(role="assistant", content="tool")
    state.pending_tool_responses = [Message(role="tool", content="done")]

    reset = store.reset_for_run(session_id="chat1", debug=False)

    assert reset is state
    assert reset.messages == []
    assert reset.assistant_with_tool_calls is None
    assert reset.pending_tool_responses == []


@pytest.mark.asyncio
async def test_state_context_restores_previous_state():
    store = AgentSessionStateStore(agent_name="agent")
    state = store.get(session_id="chat1", debug=False)
    state.state = AgentState.IDLE

    async with store.state_context(
        new_state=AgentState.RUNNING, session_id="chat1", debug=False
    ):
        assert state.state == AgentState.RUNNING

    assert state.state == AgentState.IDLE


@pytest.mark.asyncio
async def test_state_context_marks_error_then_restores_previous_state():
    store = AgentSessionStateStore(agent_name="agent")
    state = store.get(session_id="chat1", debug=False)

    with pytest.raises(RuntimeError):
        async with store.state_context(
            new_state=AgentState.RUNNING, session_id="chat1", debug=False
        ):
            raise RuntimeError("boom")

    assert state.state == AgentState.IDLE


@pytest.mark.asyncio
async def test_state_context_rejects_invalid_state():
    store = AgentSessionStateStore(agent_name="agent")

    with pytest.raises(ValueError, match="Invalid agent state"):
        async with store.state_context(
            new_state="running", session_id="chat1", debug=False
        ):
            pass
