from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.tool_action import AgentToolActionRunner
from omnicoreagent.core.types import AgentState, ParsedResponse, SessionState, ToolError
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


def make_session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


class ObservationHandler:
    def __init__(self):
        self.appended = None

    async def parse(self, observation):
        return observation

    def build_results_observation(
        self, tool_call_results, tools_results, session_state, session_id
    ):
        return "observation text"

    async def append_observations(self, **kwargs):
        self.appended = kwargs


class FailureHandler:
    def __init__(self):
        self.loop_state = None

    async def handle_validation_error(self, **kwargs):
        return (
            "missing_tool",
            [{"query": "x"}],
            "missing tool",
            [
                {
                    "tool_name": "missing_tool",
                    "args": {"query": "x"},
                    "status": "error",
                    "data": None,
                    "message": "missing tool",
                }
            ],
        )

    async def handle_loop_state(self, **kwargs):
        self.loop_state = kwargs


@pytest.mark.asyncio
async def test_tool_action_runner_handles_validation_error():
    observation_handler = ObservationHandler()
    failure_handler = FailureHandler()

    async def resolve(**kwargs):
        return ToolError(
            observation="missing tool",
            tool_name="missing_tool",
            tool_args={"query": "x"},
        )

    runner = AgentToolActionRunner(
        agent_name="agent",
        tool_call_resolver=SimpleNamespace(resolve=resolve),
        tool_failure_handler=failure_handler,
        tool_batch_runner=SimpleNamespace(),
        tool_observation_handler=observation_handler,
    )

    async def add_message_to_history(**kwargs):
        raise AssertionError("Validation errors do not write tool history directly")

    await runner.run(
        parsed_response=ParsedResponse(action=True),
        response="<tool_call />",
        session_state=make_session_state(),
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        reset_system_prompt=lambda **kwargs: [],
        session_id="chat1",
    )

    assert (
        observation_handler.appended["tools_results"][0]["tool_name"] == "missing_tool"
    )
    assert (
        failure_handler.loop_state["tool_call_results"][0].tool_name == "missing_tool"
    )


@pytest.mark.asyncio
async def test_tool_action_runner_executes_resolved_tool_batch():
    observation_handler = ObservationHandler()
    failure_handler = FailureHandler()
    calls = []
    tool_call_result = SimpleNamespace(tool_name="search", tool_args={"q": "x"})

    async def resolve(**kwargs):
        calls.append(("resolve", kwargs))
        return [tool_call_result]

    async def start(**kwargs):
        calls.append(("start", kwargs))
        return "search", [{"q": "x"}]

    async def execute(**kwargs):
        calls.append(("execute", kwargs))
        assert kwargs["parse_tool_observation"] == observation_handler.parse
        assert kwargs["build_tool_results_observation"] == (
            observation_handler.build_results_observation
        )
        return "observation text", [{"tool_name": "search", "status": "success"}]

    runner = AgentToolActionRunner(
        agent_name="agent",
        tool_call_resolver=SimpleNamespace(resolve=resolve),
        tool_failure_handler=failure_handler,
        tool_batch_runner=SimpleNamespace(start=start, execute=execute),
        tool_observation_handler=observation_handler,
    )

    async def add_message_to_history(**kwargs):
        calls.append(("history", kwargs))

    await runner.run(
        parsed_response=ParsedResponse(action=True),
        response="<tool_call />",
        session_state=make_session_state(),
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        reset_system_prompt=lambda **kwargs: [],
        session_id="chat1",
        local_tools="registry",
    )

    assert [name for name, _ in calls] == ["resolve", "start", "execute"]
    assert observation_handler.appended["tools_results"] == [
        {"tool_name": "search", "status": "success"}
    ]
    assert failure_handler.loop_state["tool_call_results"] == [tool_call_result]
