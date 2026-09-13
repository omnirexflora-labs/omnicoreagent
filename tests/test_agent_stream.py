import asyncio
from contextlib import aclosing

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.runtime.streaming import current_delivery
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.serve.sse import run_agent_stream


class StreamingModel:
    def __init__(self, turns, *, fail=False):
        self.turns = iter(turns)
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.fail = fail
        self.requests = []

    async def llm_stream(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        turn = next(self.turns)
        try:
            yield {"type": "text_delta", "text": turn.text}
            await self.release.wait()
            if self.fail:
                raise RuntimeError("provider disconnected")
            yield {"type": "turn_complete", "turn": turn}
        finally:
            self.closed.set()

    async def llm_call(self, messages, tools=None):
        return next(self.turns)


def agent_with(model, *, registry=None, name="stream-agent", children=None):
    store = InMemoryTelemetryStore()
    agent = OmniCoreAgent(
        name=name,
        system_instruction="Test",
        model_config={"provider": "openai", "model": "test", "api_key": "test"},
        agent_config={"guardrail_mode": "off"},
        local_tools=registry,
        sub_agents=children,
        telemetry_store=store,
        telemetry_recorder=TelemetryRecorder(
            store, TelemetryConfig(record_outputs=False)
        ),
    )
    agent._initialized = True
    agent.agent = BaseReactAgent(name, 5, 2, tool_offload_config={"enabled": False})
    agent.llm_connection = model
    agent.memory_router = MemoryRouter("in_memory")
    return agent


@pytest.mark.asyncio
async def test_public_stream_yields_early_and_matches_complete_run():
    model = StreamingModel([ModelTurn(content="Hello", finish_reason="stop")])
    agent = agent_with(model)
    async with aclosing(agent.stream("hello", session_id="s", run_id="r")) as stream:
        delta = await asyncio.wait_for(anext(stream), 2)
        assert delta["text"] == "Hello"
        assert delta["phase"] == "intermediate"
        assert delta["run_id"] == delta["actor_run_id"] == "r"
        assert delta["trace_id"] and delta["sequence"] == 1
        assert not model.closed.is_set()
        model.release.set()
        terminal = await anext(stream)
        assert terminal["type"] == "complete"
        assert terminal["response"] == "Hello"
        assert terminal["status"] == "success"
    complete = await agent_with(StreamingModel([ModelTurn(content="Hello")])).run(
        "hello"
    )
    for key in ("response", "status", "termination_reason", "agent_name"):
        assert terminal[key] == complete[key]
    assert model.closed.is_set()
    assert current_delivery.get() is None


@pytest.mark.asyncio
async def test_call_waits_for_complete_turn_then_continues():
    registry = ToolRegistry()
    effects = []

    @registry.register_tool(name="echo")
    async def echo(value: str):
        effects.append(value)
        return value

    model = StreamingModel(
        [
            ModelTurn(
                content="Working",
                tool_calls=(ToolRequest("call1", "echo", '{"value":"001"}'),),
                finish_reason="tool_calls",
            ),
            ModelTurn(content="Done", finish_reason="stop"),
        ]
    )
    agent = agent_with(model, registry=registry)
    async with aclosing(agent.stream("task")) as stream:
        assert (await anext(stream))["text"] == "Working"
        assert effects == []
        model.release.set()
        events = [event async for event in stream]
    assert effects == ["001"]
    assert events[-1]["response"] == "Done"
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Done"]
    result = next(
        m for m in model.requests[1][0] if isinstance(m, dict) and m["role"] == "tool"
    )
    assert result["tool_call_id"] == "call1"


@pytest.mark.asyncio
async def test_close_cancels_provider_and_partial_failure_has_one_terminal():
    model = StreamingModel([ModelTurn(content="Partial", finish_reason="stop")])
    async with aclosing(agent_with(model).stream("task")) as stream:
        await anext(stream)
    assert model.closed.is_set()
    broken = StreamingModel(
        [ModelTurn(content="Partial", finish_reason="stop")], fail=True
    )
    broken.release.set()
    events = [event async for event in agent_with(broken).stream("task")]
    assert [e["type"] for e in events] == ["text_delta", "complete"]
    assert events[-1]["status"] == "error"
    assert events[-1]["termination_reason"] == "provider_error"


@pytest.mark.asyncio
async def test_sse_text_is_live_with_debug_outputs_disabled_and_close_cancels():
    model = StreamingModel([ModelTurn(content="Visible", finish_reason="stop")])
    agent = agent_with(model)
    async with aclosing(run_agent_stream(agent, "task", "s")) as stream:
        while True:
            event = await asyncio.wait_for(anext(stream), 2)
            if event.startswith("event: text_delta"):
                assert '"text": "Visible"' in event
                break
        assert not model.closed.is_set()
    assert model.closed.is_set()


@pytest.mark.asyncio
async def test_child_deltas_keep_actor_identity():
    child_model = StreamingModel(
        [ModelTurn(content="Child answer", finish_reason="stop")]
    )
    child_model.release.set()
    child = agent_with(child_model, name="child")
    parent_model = StreamingModel(
        [
            ModelTurn(
                content="Delegating",
                tool_calls=(ToolRequest("c", "delegate_child", '{"query":"help"}'),),
                finish_reason="tool_calls",
            ),
            ModelTurn(content="Parent answer", finish_reason="stop"),
        ]
    )
    parent_model.release.set()
    parent = agent_with(parent_model, children=[child])
    events = [event async for event in parent.stream("task", run_id="root-run")]
    deltas = [e for e in events if e["type"] == "text_delta"]
    assert [e["agent_name"] for e in deltas] == [
        "stream-agent",
        "child",
        "stream-agent",
    ]
    assert all(e["run_id"] == "root-run" for e in deltas)
    assert deltas[1]["actor_run_id"] != "root-run"
    assert [e["sequence"] for e in deltas] == [1, 2, 3]
    assert events[-1]["response"] == "Parent answer"
    schema = parent_model.requests[0][1][0]["function"]["parameters"]
    assert not ({"on_event", "run_id", "session_id"} & schema["properties"].keys())


@pytest.mark.asyncio
async def test_public_delivery_bounds_producer_and_cancels_blocked_queue():
    class FastModel:
        def __init__(self):
            self.produced = 0
            self.closed = asyncio.Event()

        async def llm_stream(self, messages, tools=None):
            try:
                for _ in range(10000):
                    self.produced += 1
                    yield {"type": "text_delta", "text": "x"}
                yield {"type": "turn_complete", "turn": ModelTurn(content="done")}
            finally:
                self.closed.set()

    model = FastModel()
    async with aclosing(agent_with(model).stream("task")) as stream:
        await anext(stream)
        await asyncio.sleep(0.01)
        assert model.produced <= 258
    assert model.closed.is_set()


@pytest.mark.asyncio
async def test_closing_during_tool_records_cancelled_call():
    registry = ToolRegistry()
    started = asyncio.Event()
    stopped = asyncio.Event()

    @registry.register_tool(name="wait")
    async def wait():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    model = StreamingModel(
        [
            ModelTurn(
                content="Working",
                tool_calls=(ToolRequest("c", "wait", "{}"),),
                finish_reason="tool_calls",
            )
        ]
    )
    model.release.set()
    agent = agent_with(model, registry=registry)
    async with aclosing(agent.stream("task", session_id="s")) as stream:
        await anext(stream)
        await asyncio.wait_for(started.wait(), 2)
    assert stopped.is_set()
    messages = await agent.memory_router.get_messages("s", "stream-agent")
    tool = next(m for m in messages if m["role"] == "tool")
    assert "cancelled" in tool["content"].lower()
