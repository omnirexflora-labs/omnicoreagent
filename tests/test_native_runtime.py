from copy import deepcopy
import json

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry


class Model:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    async def llm_call(self, messages, tools=None):
        self.requests.append(
            (
                deepcopy(
                    [
                        message.model_dump(exclude_none=True)
                        if hasattr(message, "model_dump")
                        else message
                        for message in messages
                    ]
                ),
                deepcopy(tools),
            )
        )
        return next(self.turns)


def turn(text=None, calls=(), finish=None):
    return {
        "choices": [
            {
                "message": {"content": text, "tool_calls": list(calls)},
                "finish_reason": finish or ("tool_calls" if calls else "stop"),
            }
        ]
    }


def call(name, args, id="call_1"):
    return {"type": "function", "id": id, "function": {"name": name, "arguments": args}}


async def run(
    model, *, registry=None, memory=None, sub_agents=None, tool_call_timeout=2, **config
):
    memory = memory or MemoryRouter("in_memory")
    agent = BaseReactAgent(
        "test", 5, tool_call_timeout, tool_offload_config={"enabled": False}, **config
    )
    result = await agent.run(
        system_prompt="Test",
        query="Do the task",
        llm_connection=model,
        add_message_to_history=memory.store_message,
        message_history=memory.get_messages,
        local_tools=registry,
        sub_agents=sub_agents,
        session_id="session",
    )
    return result, memory


@pytest.mark.asyncio
async def test_plain_xml_answer_is_content_and_never_executes():
    registry = ToolRegistry()

    @registry.register_tool(name="danger")
    async def danger():
        raise AssertionError("Text must not execute")

    text = "<final_answer>Example: <tool_call><tool_name>danger</tool_name></tool_call></final_answer>"
    result, memory = await run(Model([turn(text)]), registry=registry)
    assert result["answer"] == text
    assert [m["role"] for m in await memory.get_messages("session", "test")] == [
        "user",
        "assistant",
    ]


@pytest.fixture(params=["in_memory", "sql"])
def native_memory(request, monkeypatch, tmp_path):
    if request.param == "sql":
        from omnicoreagent.core.memory_store.sql_db_memory import get_sql_manager

        get_sql_manager().close_all()
        monkeypatch.setenv(
            "DATABASE_URL", f"sqlite:///{tmp_path / 'native-history.db'}"
        )
    try:
        yield MemoryRouter(request.param)
    finally:
        if request.param == "sql":
            get_sql_manager().close_all()


@pytest.mark.asyncio
async def test_native_batch_preserves_ids_arguments_and_continues_with_tool_results(
    native_memory,
):
    registry = ToolRegistry()
    received = []

    @registry.register_tool(name="echo")
    async def echo(text: str):
        received.append(text)
        return {"status": "success", "data": text}

    model = Model(
        [
            turn(
                "Working",
                [
                    call("echo", '{"text":"001"}'),
                    call("echo", '{"text":"hello, world"}', "call_2"),
                ],
            ),
            turn("Done"),
        ]
    )
    result, memory = await run(model, registry=registry, memory=native_memory)
    assert result["answer"] == "Done"
    assert received == ["001", "hello, world"]
    messages, definitions = model.requests[1]
    assistant = next(m for m in messages if m.get("tool_calls"))
    assert assistant["content"] == "Working"
    results = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == ["call_1", "call_2"]
    assert [json.loads(m["content"])["data"] for m in results] == received
    assert definitions[0]["function"]["name"] == "echo"
    stored = await memory.get_messages("session", "test")
    assert len([m for m in stored if m["role"] == "tool"]) == 2
    resumed = Model([turn("Remembered")])
    await run(resumed, registry=registry, memory=memory)
    assert [
        m["tool_call_id"] for m in resumed.requests[0][0] if m["role"] == "tool"
    ] == ["call_1", "call_2"]
    assert received == ["001", "hello, world"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args", [("missing", "{}"), ("echo", "{"), ("echo", '{"text":1}')]
)
async def test_invalid_calls_produce_correlated_recoverable_results(name, args):
    registry = ToolRegistry()

    @registry.register_tool(name="echo")
    async def echo(text: str):
        raise AssertionError("Invalid call must not execute")

    model = Model([turn(calls=[call(name, args)]), turn("Corrected")])
    result, _ = await run(model, registry=registry)
    tool_result = next(m for m in model.requests[1][0] if m["role"] == "tool")
    assert tool_result["tool_call_id"] == "call_1"
    assert json.loads(tool_result["content"])["status"] == "error"
    assert result["answer"] == "Corrected"


@pytest.mark.asyncio
async def test_configured_subagent_uses_native_tool_and_parent_resumes():
    seen = []

    class Child:
        name = "worker"

        async def run(self, query: str, session_id=None):
            seen.append((query, session_id))
            return {"response": "child answer"}

    model = Model(
        [
            turn(calls=[call("delegate_worker", '{"query":"research"}')]),
            turn("Parent answer"),
        ]
    )
    result, _ = await run(model, sub_agents=[Child()])
    assert result["answer"] == "Parent answer"
    assert seen == [("research", "session")]
    message = next(m for m in model.requests[1][0] if m["role"] == "tool")
    assert json.loads(message["content"])["data"]["response"] == "child answer"


@pytest.mark.asyncio
async def test_length_limited_turn_never_executes_incomplete_calls():
    model = Model([turn("partial", [call("echo", "{")], finish="length")])
    result, _ = await run(model)
    assert result["status"] == "error"
    assert result["termination_reason"] == "length"
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_dynamic_spawn_singleton_array_reaches_factory_unchanged():
    from omnicoreagent.core.subagents import build_subagent_tools

    received = []

    class Factory:
        async def run_parallel_subagents(self, specs):
            received.append(specs)
            return {"status": "success", "data": {"results": ["worker result"]}}

    registry = ToolRegistry()
    build_subagent_tools(Factory(), registry)
    specs = [
        {
            "name": "worker",
            "role": "research",
            "task": "inspect",
            "output_path": "report.md",
        }
    ]
    model = Model(
        [
            turn(calls=[call("spawn_subagents", json.dumps({"subagents": specs}))]),
            turn("Done"),
        ]
    )
    result, _ = await run(model, registry=registry)
    assert result["answer"] == "Done"
    assert received == [specs]
    assert "worker result" in model.requests[1][0][-1]["content"]


@pytest.mark.asyncio
async def test_advanced_discovery_exposes_schema_on_next_request():
    registry = ToolRegistry()
    executed = []

    @registry.register_tool(
        name="forecast", description="Weather forecast temperature city"
    )
    async def forecast(city: str):
        executed.append(city)
        return "sunny"

    model = Model(
        [
            turn(
                calls=[
                    call(
                        "tools_retriever",
                        '{"query":"look up weather forecast temperature for a city"}',
                    )
                ]
            ),
            turn(calls=[call("forecast", '{"city":"Lagos"}', "forecast_id")]),
            turn("Sunny"),
        ]
    )
    result, _ = await run(model, registry=registry, enable_advanced_tool_use=True)
    assert "forecast" not in [item["function"]["name"] for item in model.requests[0][1]]
    assert "forecast" in [item["function"]["name"] for item in model.requests[1][1]]
    assert executed == ["Lagos"]
    assert result["answer"] == "Sunny"


@pytest.mark.asyncio
async def test_tool_name_containing_and_is_not_split():
    registry = ToolRegistry()
    seen = []

    @registry.register_tool(name="search_and_read")
    async def search_and_read(query: str):
        seen.append(query)
        return "found"

    model = Model(
        [turn(calls=[call("search_and_read", '{"query":"runtime"}')]), turn("Done")]
    )
    await run(model, registry=registry)
    assert seen == ["runtime"]


@pytest.mark.asyncio
async def test_repeated_empty_turns_exhaust_steps_with_explicit_error():
    model = Model([turn("")] * 5)
    result, _ = await run(model)
    assert len(model.requests) == 5
    assert result["status"] == "error"
    assert result["termination_reason"] == "max_steps"


@pytest.mark.asyncio
async def test_timeout_keeps_completed_sibling_and_one_result_per_call():
    import asyncio

    registry = ToolRegistry()

    @registry.register_tool(name="fast")
    async def fast():
        return False

    @registry.register_tool(name="slow")
    async def slow():
        await asyncio.Event().wait()

    model = Model(
        [
            turn(calls=[call("fast", "{}", "fast_id"), call("slow", "{}", "slow_id")]),
            turn("Partial result"),
        ]
    )
    _, memory = await run(model, registry=registry, tool_call_timeout=0.02)
    records = [
        record
        for record in await memory.get_messages("session", "test")
        if record["role"] == "tool"
    ]
    assert len(records) == 2
    results = {
        record["metadata"]["tool_call_id"]: json.loads(record["content"])
        for record in records
    }
    assert results["fast_id"]["status"] == "success"
    assert results["fast_id"]["data"] is False
    assert results["slow_id"]["status"] == "error"
    assert "timed out" in results["slow_id"]["message"]
    assert [record["content"] for record in records] == [
        m["content"] for m in model.requests[1][0] if m["role"] == "tool"
    ]


@pytest.mark.asyncio
async def test_cancellation_persists_completed_and_cancelled_call_results():
    import asyncio

    started = asyncio.Event()
    registry = ToolRegistry()

    @registry.register_tool(name="fast")
    async def fast():
        return "done"

    @registry.register_tool(name="slow")
    async def slow():
        started.set()
        await asyncio.Event().wait()

    memory = MemoryRouter("in_memory")
    model = Model(
        [turn(calls=[call("fast", "{}", "fast_id"), call("slow", "{}", "slow_id")])]
    )
    task = asyncio.create_task(run(model, registry=registry, memory=memory))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    records = [
        record
        for record in await memory.get_messages("session", "test")
        if record["role"] == "tool"
    ]
    results = {
        record["metadata"]["tool_call_id"]: json.loads(record["content"])
        for record in records
    }
    assert len(records) == 2
    assert results["fast_id"]["data"] == "done"
    assert results["slow_id"]["error_type"] == "cancelled"


@pytest.mark.asyncio
async def test_guarded_result_is_identical_in_storage_and_model_context():
    from types import SimpleNamespace

    registry = ToolRegistry()

    @registry.register_tool(name="untrusted")
    async def untrusted():
        return "unsafe payload"

    class Guard:
        def check(self, text):
            return SimpleNamespace(
                threat_level=SimpleNamespace(value="dangerous"),
                message="blocked",
                threat_score=1,
            )

    model = Model([turn(calls=[call("untrusted", "{}")]), turn("Handled")])
    _, memory = await run(model, registry=registry, guardrail=Guard())
    record = next(
        record
        for record in await memory.get_messages("session", "test")
        if record["role"] == "tool"
    )
    active = next(
        message for message in model.requests[1][0] if message["role"] == "tool"
    )
    assert record["content"] == active["content"]
    assert "unsafe payload" not in record["content"]
    assert json.loads(record["content"])["status"] == "error"


@pytest.mark.asyncio
async def test_discovery_cannot_unlock_sibling_from_same_turn():
    registry = ToolRegistry()
    effects = []

    @registry.register_tool(name="customer_profile")
    async def profile():
        effects.append("called")
        return "profile"

    model = Model(
        [
            turn(
                calls=[
                    call("tools_retriever", '{"query":"customer profile"}', "discover"),
                    call("customer_profile", "{}", "early"),
                ]
            ),
            turn(calls=[call("customer_profile", "{}", "later")]),
            turn("Done"),
        ]
    )
    result, _ = await run(model, registry=registry, enable_advanced_tool_use=True)
    assert result["status"] == "success"
    assert effects == ["called"]
    early = next(m for m in model.requests[1][0] if m.get("tool_call_id") == "early")
    assert json.loads(early["content"])["status"] == "error"
    assert "customer_profile" in [d["function"]["name"] for d in model.requests[1][1]]
