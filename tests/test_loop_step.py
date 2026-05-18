from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.loop_step import AgentLoopStepHandler
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.types import AgentState, ParsedResponse, SessionState
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


def make_session_state() -> SessionState:
    return SessionState(
        messages=[],
        state=AgentState.RUNNING,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


class OutcomeHandler:
    def __init__(self):
        self.final_answer_kwargs = None
        self.max_steps_kwargs = None

    async def handle_final_answer(self, **kwargs):
        self.final_answer_kwargs = kwargs
        return {"answer": kwargs["answer"], "usage": kwargs["run_usage"]}

    def max_steps_result(self, **kwargs):
        self.max_steps_kwargs = kwargs
        return {"answer": "MAX_STEPS_REACHED", "usage": kwargs["run_usage"]}


class ToolActionRunner:
    def __init__(self):
        self.kwargs = None

    async def run(self, **kwargs):
        self.kwargs = kwargs


class SubAgentRunner:
    def __init__(self):
        self.kwargs = None

    async def execute(self, **kwargs):
        self.kwargs = kwargs


def make_handler(
    *,
    outcome_handler: OutcomeHandler | None = None,
    tool_action_runner: ToolActionRunner | None = None,
    subagent_runner: SubAgentRunner | None = None,
    max_steps: int = 5,
) -> AgentLoopStepHandler:
    return AgentLoopStepHandler(
        agent_name="agent",
        max_steps=max_steps,
        run_outcome_handler=outcome_handler or OutcomeHandler(),
        tool_action_runner=tool_action_runner or ToolActionRunner(),
        subagent_runner=subagent_runner or SubAgentRunner(),
        reset_system_prompt=lambda messages, system_prompt: messages,
    )


@pytest.mark.asyncio
async def test_loop_step_handles_final_answer():
    outcome_handler = OutcomeHandler()
    handler = make_handler(outcome_handler=outcome_handler)
    run_usage = Usage()

    result = await handler.handle(
        parsed_response=ParsedResponse(answer="done"),
        response="<final_answer>done</final_answer>",
        session_state=make_session_state(),
        add_message_to_history=SimpleNamespace(),
        system_prompt="system",
        session_id="chat1",
        run_usage=run_usage,
        start_time=1.0,
        current_steps=1,
        last_valid_response=None,
    )

    assert result.should_return is True
    assert result.run_result == {"answer": "done", "usage": run_usage}
    assert result.last_valid_response == "done"
    assert outcome_handler.final_answer_kwargs["answer"] == "done"
    assert outcome_handler.final_answer_kwargs["session_id"] == "chat1"


@pytest.mark.asyncio
async def test_loop_step_dispatches_subagent_calls():
    subagent_runner = SubAgentRunner()
    handler = make_handler(subagent_runner=subagent_runner)
    agent_calls = [{"name": "worker", "task": "check docs"}]
    run_usage = Usage()
    session_state = make_session_state()

    result = await handler.handle(
        parsed_response=ParsedResponse(
            action=True,
            agent_calls=True,
            data=agent_calls,
        ),
        response="<agent_calls />",
        session_state=session_state,
        add_message_to_history=SimpleNamespace(),
        system_prompt="system",
        session_id="chat1",
        run_usage=run_usage,
        start_time=1.0,
        current_steps=1,
        last_valid_response="previous",
        sub_agents=["worker"],
    )

    assert result.should_return is False
    assert result.last_valid_response == "previous"
    assert subagent_runner.kwargs["agent_calls"] == agent_calls
    assert subagent_runner.kwargs["sub_agents"] == ["worker"]
    assert subagent_runner.kwargs["session_state"] is session_state
    assert subagent_runner.kwargs["run_usage"] is run_usage


@pytest.mark.asyncio
async def test_loop_step_dispatches_tool_action():
    tool_action_runner = ToolActionRunner()
    handler = make_handler(tool_action_runner=tool_action_runner)
    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data='[{"tool": "search", "parameters": {"query": "runtime"}}]',
    )
    session_state = make_session_state()

    result = await handler.handle(
        parsed_response=parsed_response,
        response="<tool_calls />",
        session_state=session_state,
        add_message_to_history=SimpleNamespace(),
        system_prompt="system",
        session_id="chat1",
        run_usage=Usage(),
        start_time=1.0,
        current_steps=1,
        last_valid_response=None,
        sessions={"server": "session"},
        mcp_tools={"server": [{"name": "search"}]},
        local_tools="registry",
        sub_agents=["worker"],
    )

    assert result.should_return is False
    assert tool_action_runner.kwargs["parsed_response"] is parsed_response
    assert tool_action_runner.kwargs["session_state"] is session_state
    assert tool_action_runner.kwargs["local_tools"] == "registry"
    assert tool_action_runner.kwargs["mcp_tools"] == {"server": [{"name": "search"}]}
    assert tool_action_runner.kwargs["sub_agents"] == ["worker"]


@pytest.mark.asyncio
async def test_loop_step_appends_parser_error():
    handler = make_handler()
    session_state = make_session_state()

    result = await handler.handle(
        parsed_response=ParsedResponse(error="Response must use XML format."),
        response="plain text",
        session_state=session_state,
        add_message_to_history=SimpleNamespace(),
        system_prompt="system",
        session_id="chat1",
        run_usage=Usage(),
        start_time=1.0,
        current_steps=1,
        last_valid_response="previous",
    )

    assert result.should_return is False
    assert result.last_valid_response == "previous"
    assert session_state.messages[-1].role == "user"
    assert session_state.messages[-1].content == "Response must use XML format."


@pytest.mark.asyncio
async def test_loop_step_returns_max_steps_result():
    outcome_handler = OutcomeHandler()
    handler = make_handler(outcome_handler=outcome_handler, max_steps=2)
    run_usage = Usage()
    session_state = make_session_state()

    result = await handler.handle(
        parsed_response=ParsedResponse(action=True, tool_calls=True, data="[]"),
        response="<tool_calls />",
        session_state=session_state,
        add_message_to_history=SimpleNamespace(),
        system_prompt="system",
        session_id="chat1",
        run_usage=run_usage,
        start_time=1.0,
        current_steps=2,
        last_valid_response="partial",
    )

    assert result.should_return is True
    assert result.run_result == {"answer": "MAX_STEPS_REACHED", "usage": run_usage}
    assert result.last_valid_response == "partial"
    assert session_state.state == AgentState.STUCK
    assert outcome_handler.max_steps_kwargs == {
        "max_steps": 2,
        "last_valid_response": "partial",
        "run_usage": run_usage,
    }
