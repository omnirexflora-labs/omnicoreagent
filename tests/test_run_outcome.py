from __future__ import annotations

import pytest

from omnicoreagent.core.agents.run_outcome import AgentRunOutcomeHandler
from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.types import AgentState, SessionState
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


def make_session_state():
    return SessionState(
        messages=[],
        state=AgentState.RUNNING,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


@pytest.mark.asyncio
async def test_handle_final_answer_updates_state_history_event_and_usage():
    handler = AgentRunOutcomeHandler(agent_name="agent")
    session_state = make_session_state()
    history = []
    events = []
    run_usage = Usage()

    async def add_message_to_history(**kwargs):
        history.append(kwargs)

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    result = await handler.handle_final_answer(
        answer="done",
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat1",
        event_router=event_router,
        run_usage=run_usage,
        start_time=0,
    )

    assert result["answer"] == "done"
    assert result["usage"] is run_usage
    assert run_usage.total_time > 0
    assert session_state.state == AgentState.FINISHED
    assert session_state.messages[-1].content == "done"
    assert history == [
        {
            "role": "assistant",
            "content": "done",
            "session_id": "chat1",
            "metadata": {"agent_name": "agent"},
        }
    ]
    assert events[0]["event"].type == EventType.FINAL_ANSWER
    assert events[0]["event"].payload.message == "done"


def test_max_steps_result_preserves_last_valid_response():
    run_usage = Usage()
    result = AgentRunOutcomeHandler("agent").max_steps_result(
        max_steps=5,
        last_valid_response="partial",
        run_usage=run_usage,
    )

    assert "MAX_STEPS_REACHED" in result["answer"]
    assert result["answer"].endswith("partial")
    assert result["usage"] is run_usage


def test_max_steps_result_handles_no_valid_response():
    result = AgentRunOutcomeHandler("agent").max_steps_result(
        max_steps=5,
        last_valid_response=None,
        run_usage=Usage(),
    )

    assert "without valid response" in result["answer"]


def test_loop_stuck_result_requires_last_valid_response():
    handler = AgentRunOutcomeHandler("agent")

    assert handler.loop_stuck_result(None) is None
    assert handler.loop_stuck_result("partial").endswith("partial")
