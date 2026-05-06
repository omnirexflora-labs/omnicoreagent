from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.initial_messages import AgentInitialMessagePreparer
from omnicoreagent.core.types import AgentState, SessionState
from omnicoreagent.core.utils import RobustLoopDetector


def make_session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


class PromptBuilder:
    def __init__(self):
        self.injected = False
        self.calls = []

    async def build_system_prompt(self, **kwargs):
        self.calls.append(kwargs)
        return f"{kwargs['base_system_prompt']}\n{kwargs['tools_section']}"

    def inject_current_datetime(self, messages):
        self.injected = True


@pytest.mark.asyncio
async def test_initial_message_preparer_loads_tools_history_and_system_prompt():
    session_state = make_session_state()
    prompt_builder = PromptBuilder()
    history_loaded = False

    async def render_prompt_registry(**kwargs):
        assert kwargs["mcp_tools"] == {"server": []}
        assert kwargs["local_tools"] == "registry"
        return "tools section"

    async def load(**kwargs):
        nonlocal history_loaded
        history_loaded = True
        assert kwargs["session_id"] == "chat1"

    preparer = AgentInitialMessagePreparer(
        tool_runtime_registry=SimpleNamespace(
            render_prompt_registry=render_prompt_registry
        ),
        message_history_loader=SimpleNamespace(load=load),
        prompt_context_builder=prompt_builder,
    )

    await preparer.prepare(
        session_state=session_state,
        system_prompt="system",
        session_id="chat1",
        message_history=lambda: [],
        mcp_tools={"server": []},
        local_tools="registry",
        sub_agents=["agent"],
    )

    assert history_loaded is True
    assert prompt_builder.injected is True
    assert session_state.messages[0].role == "system"
    assert session_state.messages[0].content == "system\ntools section"
    assert prompt_builder.calls[0]["sub_agents"] == ["agent"]


@pytest.mark.asyncio
async def test_initial_message_preparer_falls_back_when_tools_fail():
    session_state = make_session_state()
    prompt_builder = PromptBuilder()

    async def render_prompt_registry(**kwargs):
        raise RuntimeError("tool render failed")

    async def load(**kwargs):
        return None

    preparer = AgentInitialMessagePreparer(
        tool_runtime_registry=SimpleNamespace(
            render_prompt_registry=render_prompt_registry
        ),
        message_history_loader=SimpleNamespace(load=load),
        prompt_context_builder=prompt_builder,
    )

    await preparer.prepare(
        session_state=session_state,
        system_prompt="system",
        session_id="chat1",
        message_history=lambda: [],
    )

    assert session_state.messages[0].content == "system\nNo tools available"


@pytest.mark.asyncio
async def test_initial_message_preparer_times_out_to_defaults():
    session_state = make_session_state()
    prompt_builder = PromptBuilder()

    async def render_prompt_registry(**kwargs):
        await asyncio.sleep(0.05)
        return "late tools"

    async def load(**kwargs):
        await asyncio.sleep(0.05)

    preparer = AgentInitialMessagePreparer(
        tool_runtime_registry=SimpleNamespace(
            render_prompt_registry=render_prompt_registry
        ),
        message_history_loader=SimpleNamespace(load=load),
        prompt_context_builder=prompt_builder,
        timeout_seconds=0.001,
    )

    await preparer.prepare(
        session_state=session_state,
        system_prompt="system",
        session_id="chat1",
        message_history=lambda: [],
    )

    assert session_state.messages[0].content == "system\nNo tools available"
