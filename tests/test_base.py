from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
import json
from omnicoreagent.core.agents.base import BaseReactAgent, TOOL_CALL_TIMEOUT_MESSAGE
from omnicoreagent.core.events.base import EventType
from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.types import AgentState, Message, ParsedResponse, ToolCallResult, ToolError


@pytest.fixture
def agent():
    return BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
        request_limit=5,
        total_tokens_limit=1000,
    )


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_final_answer(agent):
    response = "<final_answer>It is sunny today.</final_answer>"
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.answer == "It is sunny today."


@pytest.mark.asyncio
async def test_extract_action_or_answer_with_action(agent):
    response = '<tool_call><tool_name>search</tool_name><parameters>{"input": "news"}</parameters></tool_call>'
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.action is True
    assert result.tool_calls is True
    # data is a json string
    data = json.loads(result.data)
    assert data[0]["tool"] == "search"
    assert data[0]["parameters"]["input"] == "news"


@pytest.mark.broken_upstream
@pytest.mark.asyncio
async def test_extract_action_or_answer_fallback_error(agent):
    response = "This is just a general response without XML."
    result = await agent.extract_action_or_answer(
        response, session_id="test", event_router=AsyncMock()
    )
    assert result.error is not None
    assert "Response must use XML format" in result.error


@pytest.mark.asyncio
async def test_update_llm_working_memory_empty(agent):
    message_history = AsyncMock(return_value=[])
    await agent.update_llm_working_memory(
        message_history=message_history,
        session_id="chat456",
        llm_connection=AsyncMock(),
        debug=False,
    )
    session_state = agent._get_session_state("chat456", debug=False)
    assert len(session_state.messages) == 0


@pytest.mark.asyncio
async def test_act_executes_tool_name_with_and_as_literal(agent):
    registry = ToolRegistry()
    history = []

    @registry.register_tool(
        name="search_and_read",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        description="Search and read in one call.",
    )
    async def search_and_read(query: str):
        return {"status": "success", "data": f"result for {query}"}

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data=json.dumps(
            [{"tool": "search_and_read", "parameters": {"query": "runtime"}}]
        ),
    )

    await agent.act(
        parsed_response=parsed_response,
        response="<tool_call><tool_name>search_and_read</tool_name></tool_call>",
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        sessions={},
        local_tools=registry,
        session_id="chat789",
    )

    tool_messages = [item for item in history if item["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["metadata"]["tool"] == "search_and_read"
    assert "result for runtime" in tool_messages[0]["content"]

    observations = [item for item in history if item["role"] == "user"]
    assert observations
    assert 'tool_name="search_and_read#1"' in observations[-1]["content"]


@pytest.mark.asyncio
async def test_act_records_one_tool_call_id_per_parallel_tool(agent):
    registry = ToolRegistry()
    history = []

    @registry.register_tool(
        name="alpha",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Alpha tool.",
    )
    async def alpha(value: str):
        return {"status": "success", "data": f"alpha:{value}"}

    @registry.register_tool(
        name="beta",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Beta tool.",
    )
    async def beta(value: str):
        return {"status": "success", "data": f"beta:{value}"}

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    parsed_response = ParsedResponse(
        action=True,
        tool_calls=True,
        data=json.dumps(
            [
                {"tool": "alpha", "parameters": {"value": "one"}},
                {"tool": "beta", "parameters": {"value": "two"}},
            ]
        ),
    )

    await agent.act(
        parsed_response=parsed_response,
        response="<tool_calls>...</tool_calls>",
        add_message_to_history=add_message_to_history,
        system_prompt="system",
        sessions={},
        local_tools=registry,
        session_id="chat790",
    )

    assistant_message = next(item for item in history if item["role"] == "assistant")
    tool_calls = assistant_message["metadata"]["tool_calls"]
    tool_messages = [item for item in history if item["role"] == "tool"]

    assert [call["function"]["name"] for call in tool_calls] == ["alpha", "beta"]
    assert [item["metadata"]["tool"] for item in tool_messages] == ["alpha", "beta"]
    assert {call["id"] for call in tool_calls} == {
        item["metadata"]["tool_call_id"] for item in tool_messages
    }


@pytest.mark.asyncio
async def test_run_prepares_internal_tools_once_for_prompt_and_execution(monkeypatch):
    agent = BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
        enable_advanced_tool_use=True,
    )
    build_count = 0
    history = []

    async def fake_build_internal_tools(registry):
        nonlocal build_count
        build_count += 1

        @registry.register_tool(
            name="internal_ping",
            inputSchema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            description="Internal ping tool.",
        )
        async def internal_ping(value: str):
            return {"status": "success", "data": f"pong:{value}"}

        return registry

    monkeypatch.setattr(
        "omnicoreagent.core.agents.base.build_tool_registry_advance_tools_use",
        fake_build_internal_tools,
    )

    class FakeLLMConnection:
        def __init__(self):
            self.calls = 0

        async def llm_call(self, messages):
            self.calls += 1
            if self.calls == 1:
                assert "internal_ping" in messages[0].content
                return """
<thought>Need internal tool.</thought>
<tool_call>
  <tool_name>internal_ping</tool_name>
  <parameters>
    <value>runtime</value>
  </parameters>
</tool_call>
"""
            return "<final_answer>done</final_answer>"

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(agent_name, session_id):
        return history

    result = await agent.run(
        system_prompt="system",
        query="run internal ping",
        llm_connection=FakeLLMConnection(),
        add_message_to_history=add_message_to_history,
        message_history=message_history,
        session_id="chat791",
    )

    tool_messages = [item for item in history if item["role"] == "tool"]
    assert result["answer"] == "done"
    assert build_count == 1
    assert len(tool_messages) == 1
    assert tool_messages[0]["metadata"]["tool"] == "internal_ping"
    assert "pong:runtime" in tool_messages[0]["content"]


def test_maybe_offload_tool_result_replaces_large_regular_output(agent, tmp_path):
    agent.tool_offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10_000},
        base_dir=str(tmp_path),
    )
    result = {
        "tool_name": "search_docs",
        "args": {"query": "runtime"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }

    processed = agent._maybe_offload_tool_result(result=result, session_id="chat792")

    assert processed is result
    assert "[TOOL RESPONSE OFFLOADED]" in result["data"]
    assert "Tool: search_docs" in result["data"]
    assert agent.tool_offloader.get_stats()["offload_count"] == 1


def test_maybe_offload_tool_result_keeps_artifact_tool_output_inline(agent, tmp_path):
    agent.tool_offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10_000},
        base_dir=str(tmp_path),
    )
    result = {
        "tool_name": "read_artifact",
        "args": {"artifact_id": "artifact_1"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }

    processed = agent._maybe_offload_tool_result(result=result, session_id="chat793")

    assert processed is result
    assert result["data"] == "x" * 80
    assert agent.tool_offloader.get_stats()["offload_count"] == 0


def test_build_tool_results_observation_formats_parallel_results(agent):
    session_state = agent._get_session_state(session_id="chat794", debug=False)
    tool_calls = [
        ToolCallResult(tool_executor=None, tool_name="alpha", tool_args={}),
        ToolCallResult(tool_executor=None, tool_name="beta", tool_args={}),
    ]
    tools_results = [
        {
            "tool_name": "alpha",
            "args": {},
            "status": "success",
            "data": "alpha result",
            "message": None,
        },
        {
            "tool_name": "beta",
            "args": {},
            "status": "error",
            "data": None,
            "message": "beta failed",
        },
    ]

    observation = agent._build_tool_results_observation(
        tool_call_results=tool_calls,
        tools_results=tools_results,
        session_state=session_state,
        session_id="chat794",
    )

    assert observation == "Partial success:\nalpha#1: alpha result\n\nbeta#1 ERROR: beta failed"


@pytest.mark.asyncio
async def test_handle_tool_execution_error_records_history_and_event(agent):
    history = []
    events = []
    session_state = agent._get_session_state(session_id="chat795", debug=False)
    tool_calls = [
        ToolCallResult(
            tool_executor=None,
            tool_name="alpha",
            tool_args={"value": "one"},
            tool_call_id="tool-call-alpha",
        ),
        ToolCallResult(
            tool_executor=None,
            tool_name="beta",
            tool_args={"value": "two"},
            tool_call_id="tool-call-beta",
        ),
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    results = await agent._handle_tool_execution_error(
        tool_call_results=tool_calls,
        error_message=TOOL_CALL_TIMEOUT_MESSAGE,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat795",
        event_router=event_router,
        tool_batch_name="alpha, beta",
    )

    assert [item["role"] for item in history] == ["tool", "tool"]
    assert [item["metadata"]["tool"] for item in history] == ["alpha", "beta"]
    assert {item["metadata"]["tool_call_id"] for item in history} == {
        "tool-call-alpha",
        "tool-call-beta",
    }
    assert results == [
        {
            "tool_name": "alpha",
            "args": {"value": "one"},
            "status": "error",
            "data": None,
            "message": TOOL_CALL_TIMEOUT_MESSAGE,
        },
        {
            "tool_name": "beta",
            "args": {"value": "two"},
            "status": "error",
            "data": None,
            "message": TOOL_CALL_TIMEOUT_MESSAGE,
        },
    ]
    assert len(events) == 1
    assert events[0]["session_id"] == "chat795"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "alpha, beta"


@pytest.mark.asyncio
async def test_handle_tool_validation_error_records_loop_and_event(agent):
    events = []
    session_state = agent._get_session_state(session_id="chat796", debug=False)

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    tool_batch_name, tool_batch_args, obs_text, results = (
        await agent._handle_tool_validation_error(
            tool_error=ToolError(
                observation="The tool named 'missing_tool' does not exist.",
                tool_name="missing_tool",
                tool_args={"query": "runtime"},
            ),
            session_state=session_state,
            session_id="chat796",
            event_router=event_router,
        )
    )

    assert tool_batch_name == "missing_tool"
    assert tool_batch_args == [{"query": "runtime"}]
    assert obs_text == "The tool named 'missing_tool' does not exist."
    assert results == [
        {
            "tool_name": "missing_tool",
            "args": {"query": "runtime"},
            "status": "error",
            "data": None,
            "message": "The tool named 'missing_tool' does not exist.",
        }
    ]
    assert len(events) == 1
    assert events[0]["session_id"] == "chat796"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "missing_tool"


@pytest.mark.asyncio
async def test_start_tool_call_batch_records_assistant_and_started_event(agent):
    history = []
    events = []
    session_state = agent._get_session_state(session_id="chat797", debug=False)
    tool_calls = [
        ToolCallResult(tool_executor=None, tool_name="alpha", tool_args={"value": "1"}),
        ToolCallResult(tool_executor=None, tool_name="beta", tool_args={"value": "2"}),
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    tool_batch_name, tool_batch_args = await agent._start_tool_call_batch(
        tool_call_results=tool_calls,
        response="<tool_calls>...</tool_calls>",
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat797",
        event_router=event_router,
    )

    assert tool_batch_name == "alpha, beta"
    assert tool_batch_args == [{"value": "1"}, {"value": "2"}]
    assert all(tool.tool_call_id for tool in tool_calls)
    assert len({tool.tool_call_id for tool in tool_calls}) == 2
    assert history[0]["role"] == "assistant"
    assert [call["function"]["name"] for call in history[0]["metadata"]["tool_calls"]] == [
        "alpha",
        "beta",
    ]
    assert session_state.messages[-1].role == "assistant"
    assert len(events) == 1
    assert events[0]["event"].type == EventType.TOOL_CALL_STARTED
    assert events[0]["event"].payload.tool_name == "alpha, beta"


@pytest.mark.asyncio
async def test_execute_tool_call_batch_returns_observation_and_result_event(agent):
    history = []
    events = []
    session_state = agent._get_session_state(session_id="chat798", debug=False)

    class FakeExecutor:
        async def execute(
            self,
            agent_name,
            tool_args,
            tool_name,
            tool_call_id,
            add_message_to_history,
            session_id,
        ):
            await add_message_to_history(
                role="tool",
                content=f"{tool_name}:{tool_args['value']}",
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "args": tool_args,
                    "agent_name": agent_name,
                },
                session_id=session_id,
            )
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "success",
                "data": f"{tool_name}:{tool_args['value']}",
                "message": None,
            }

    tool_calls = [
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="alpha",
            tool_args={"value": "one"},
            tool_call_id="tool-call-alpha",
        ),
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="beta",
            tool_args={"value": "two"},
            tool_call_id="tool-call-beta",
        ),
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    obs_text, tools_results = await agent._execute_tool_call_batch(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat798",
        event_router=event_router,
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"value": "one"}, {"value": "two"}],
    )

    assert obs_text == "alpha#1: alpha:one\n\nbeta#1: beta:two"
    assert [result["tool_name"] for result in tools_results] == ["alpha", "beta"]
    assert [item["metadata"]["tool_call_id"] for item in history] == [
        "tool-call-alpha",
        "tool-call-beta",
    ]
    assert len(events) == 1
    assert events[0]["event"].type == EventType.TOOL_CALL_RESULT
    assert events[0]["event"].payload.tool_name == "alpha, beta"


@pytest.mark.asyncio
async def test_append_tool_observations_writes_history_and_sets_observing(agent):
    history = []
    session_state = agent._get_session_state(session_id="chat799", debug=False)

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    xml_obs_block = await agent._append_tool_observations(
        tools_results=[
            {
                "tool_name": "alpha",
                "args": {},
                "status": "success",
                "data": "alpha result",
                "message": None,
            }
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat799",
        debug=False,
    )

    assert 'tool_name="alpha#1"' in xml_obs_block
    assert session_state.messages[-1].role == "user"
    assert session_state.messages[-1].content == xml_obs_block
    assert session_state.state == AgentState.OBSERVING
    assert history == [
        {
            "role": "user",
            "content": xml_obs_block,
            "metadata": {"agent_name": "test_agent"},
            "session_id": "chat799",
        }
    ]


@pytest.mark.asyncio
async def test_handle_tool_loop_state_marks_session_stuck(agent):
    events = []
    session_state = agent._get_session_state(session_id="chat800", debug=False)
    session_state.messages = [Message(role="system", content="system")]

    class FakeLoopDetector:
        def __init__(self):
            self.reset_tool_name = None

        def is_looping(self, tool_name):
            return tool_name == "alpha"

        def get_loop_type(self, tool_name):
            return ["consecutive_calls"]

        def reset(self, tool_name=None):
            self.reset_tool_name = tool_name

    session_state.loop_detector = FakeLoopDetector()

    async def event_router(session_id, event):
        events.append({"session_id": session_id, "event": event})

    await agent._handle_tool_loop_state(
        tool_call_results=[
            ToolCallResult(
                tool_executor=None,
                tool_name="alpha",
                tool_args={},
                tool_call_id="tool-call-alpha",
            )
        ],
        session_state=session_state,
        system_prompt="system",
        session_id="chat800",
        event_router=event_router,
        debug=False,
    )

    assert session_state.state == AgentState.STUCK
    assert session_state.loop_detector.reset_tool_name == "alpha"
    assert session_state.messages[0].role == "system"
    assert "Tool call loop detected" in session_state.messages[-1].content
    assert len(events) == 1
    assert events[0]["session_id"] == "chat800"
    assert events[0]["event"].type == EventType.TOOL_CALL_ERROR
    assert events[0]["event"].payload.tool_name == "alpha"


@pytest.mark.asyncio
async def test_resolve_single_tool_action_uses_local_registry(agent):
    registry = ToolRegistry()

    @registry.register_tool(
        name="local_ping",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Local ping tool.",
    )
    async def local_ping(value: str):
        return {"status": "success", "data": value}

    resolved = await agent._resolve_single_tool_action(
        action={"tool": "local_ping", "parameters": {"value": "runtime"}},
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )

    assert isinstance(resolved, ToolCallResult)
    assert resolved.tool_name == "local_ping"
    assert resolved.tool_args == {"value": "runtime"}


@pytest.mark.asyncio
async def test_resolve_single_tool_action_uses_mcp_tools_before_local(agent):
    registry = ToolRegistry()

    @registry.register_tool(
        name="search",
        inputSchema={"type": "object", "properties": {}, "required": []},
        description="Local search fallback.",
    )
    async def search():
        return {"status": "success", "data": "local"}

    resolved = await agent._resolve_single_tool_action(
        action={"tool": "search", "parameters": {"query": "runtime"}},
        sessions={"server": {"session": object()}},
        mcp_tools={"server": [SimpleNamespace(name="search")]},
        local_tools=registry,
    )

    assert isinstance(resolved, ToolCallResult)
    assert resolved.tool_name == "search"
    assert resolved.tool_args == {"query": "runtime"}
    assert resolved.tool_executor.tool_handler.server_name == "server"


@pytest.mark.asyncio
async def test_resolve_single_tool_action_rejects_tool_call_to_sub_agent(agent):
    sub_agent = SimpleNamespace(name="research_agent")

    resolved = await agent._resolve_single_tool_action(
        action={"tool": "research_agent", "parameters": {"task": "inspect"}},
        sessions={},
        mcp_tools=None,
        local_tools=None,
        sub_agents=[sub_agent],
    )

    assert isinstance(resolved, ToolError)
    assert resolved.tool_name == "N/A"
    assert "is a sub-agent, not a tool" in resolved.observation
