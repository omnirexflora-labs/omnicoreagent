import pytest
import asyncio

from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.tool_batch_runner import (
    TOOL_CALL_TIMEOUT_MESSAGE,
    ToolBatchRunner,
)
from omnicoreagent.core.types import AgentState, SessionState, ToolCallResult
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


@pytest.fixture
def runner():
    return ToolBatchRunner(agent_name="test_agent", tool_call_timeout=10)


@pytest.fixture
def session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


@pytest.mark.asyncio
async def test_handle_execution_error_records_history(runner, session_state):
    history = []
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

    results = await runner.handle_execution_error(
        tool_call_results=tool_calls,
        error_message=TOOL_CALL_TIMEOUT_MESSAGE,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat795",
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


@pytest.mark.asyncio
async def test_start_records_assistant_tool_call_metadata(runner, session_state):
    history = []
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

    tool_batch_name, tool_batch_args = await runner.start(
        tool_call_results=tool_calls,
        response="<tool_calls>...</tool_calls>",
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat797",
    )

    assert tool_batch_name == "alpha, beta"
    assert tool_batch_args == [{"value": "1"}, {"value": "2"}]
    assert all(tool.tool_call_id for tool in tool_calls)
    assert len({tool.tool_call_id for tool in tool_calls}) == 2
    assert history[0]["role"] == "assistant"
    assert [
        call["function"]["name"] for call in history[0]["metadata"]["tool_calls"]
    ] == [
        "alpha",
        "beta",
    ]
    assert session_state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_execute_returns_observation_and_results(runner, session_state):
    history = []

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

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return "\n\n".join(
            f"{result['tool_name']}#1: {result['data']}" for result in tools_results
        )

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat798",
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"value": "one"}, {"value": "two"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert obs_text == "alpha#1: alpha:one\n\nbeta#1: beta:two"
    assert [result["tool_name"] for result in tools_results] == ["alpha", "beta"]
    assert [item["metadata"]["tool_call_id"] for item in history] == [
        "tool-call-alpha",
        "tool-call-beta",
    ]


@pytest.mark.asyncio
async def test_execute_timeout_records_error_for_each_tool(session_state):
    runner = ToolBatchRunner(agent_name="test_agent", tool_call_timeout=0.01)
    history = []

    class SlowExecutor:
        async def execute(
            self,
            agent_name,
            tool_args,
            tool_name,
            tool_call_id,
            add_message_to_history,
            session_id,
        ):
            await asyncio.sleep(1)

    tool_calls = [
        ToolCallResult(
            tool_executor=SlowExecutor(),
            tool_name="alpha",
            tool_args={"value": "one"},
            tool_call_id="tool-call-alpha",
        ),
        ToolCallResult(
            tool_executor=SlowExecutor(),
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

    async def parse_tool_observation(raw_output):
        raise AssertionError("Timed out tools should not be parsed")

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        raise AssertionError("Timed out tools should not build observations")

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat799",
        tool_batch_name="alpha, beta",
        tool_batch_args=[{"value": "one"}, {"value": "two"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert obs_text == TOOL_CALL_TIMEOUT_MESSAGE
    assert [result["status"] for result in tools_results] == ["error", "error"]
    assert [item["metadata"]["tool"] for item in history] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_execute_records_mcp_workspace_and_observation_telemetry(
    runner, session_state
):
    history = []
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-tool-batch-shapes",
        run_id="run-tool-batch-shapes",
        session_id="chat800",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

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
                content=f"{tool_name}:ok",
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
                "data": f"{tool_name}:ok",
                "message": None,
            }

    tool_calls = [
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="search_docs",
            tool_args={"query": "telemetry"},
            tool_call_id="tool-call-mcp",
            tool_provider="mcp",
            tool_server="docs-server",
        ),
        ToolCallResult(
            tool_executor=FakeExecutor(),
            tool_name="workspace_file_view",
            tool_args={"path": "notes.md"},
            tool_call_id="tool-call-workspace",
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

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return "[TOOL RESPONSE OFFLOADED] workspace://tool-output"

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat800",
        tool_batch_name="search_docs, workspace_file_view",
        tool_batch_args=[{"query": "telemetry"}, {"path": "notes.md"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert obs_text == "[TOOL RESPONSE OFFLOADED] workspace://tool-output"
    assert [result["tool_name"] for result in tools_results] == [
        "search_docs",
        "workspace_file_view",
    ]
    assert {span.kind for span in trace.spans} >= {
        "tool.batch",
        "mcp.tool.call",
        "workspace.read",
        "observation.pipeline",
    }
    assert {event.event_type for event in trace.events} >= {
        "tool_batch_start",
        "mcp_tool_call",
        "mcp_tool_result",
        "workspace_read",
        "observation_pipeline_start",
        "observation_pipeline_end",
        "workspace_offload",
        "tool_batch_end",
    }
    assert [event.event_type for event in trace.events].count("workspace_read") == 1
    mcp_call = next(event for event in trace.events if event.event_type == "mcp_tool_call")
    assert mcp_call.actor.type == ActorType.MCP_SERVER
    assert mcp_call.actor.name == "docs-server"


@pytest.mark.asyncio
async def test_artifact_tools_are_recorded_as_workspace_reads(runner, session_state):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-artifact-read",
        run_id="run-artifact-read",
        session_id="chat-artifact",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

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
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "success",
                "data": "artifact content",
                "message": None,
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "search_123"},
                tool_call_id="tool-call-artifact",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact",
        tool_batch_name="read_artifact",
        tool_batch_args=[{"artifact_id": "search_123"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    artifact_span = next(span for span in trace.spans if span.name == "read_artifact")
    artifact_event = next(
        event
        for event in trace.events
        if event.event_type == "workspace_read"
        and event.actor.name == "read_artifact"
    )
    assert artifact_span.kind == "workspace.read"
    assert artifact_event.input["tool_args"] == {"artifact_id": "search_123"}


@pytest.mark.asyncio
async def test_workspace_tool_telemetry_respects_tool_result_suppression(
    runner, session_state
):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(record_tool_results=False))
    context = await recorder.start_trace(
        trace_id="trace-workspace-redaction",
        run_id="run-workspace-redaction",
        session_id="chat801",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

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
                content="secret file contents",
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
                "data": "secret file contents",
                "message": None,
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return "workspace observation"

    await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="workspace_file_view",
                tool_args={"path": "notes.md"},
                tool_call_id="tool-call-workspace-redacted",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat801",
        tool_batch_name="workspace_file_view",
        tool_batch_args=[{"path": "notes.md"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    workspace_span = next(span for span in trace.spans if span.kind == "workspace.read")
    workspace_event = next(
        event for event in trace.events if event.event_type == "workspace_read"
    )
    observation_span = next(
        span for span in trace.spans if span.kind == "observation.pipeline"
    )
    tool_batch_span = next(span for span in trace.spans if span.kind == "tool.batch")
    observation_event = next(
        event
        for event in trace.events
        if event.event_type == "observation_pipeline_end"
    )
    assert workspace_span.output is None
    assert workspace_event.output is None
    assert observation_span.output is None
    assert tool_batch_span.output is None
    assert observation_event.output is None


@pytest.mark.asyncio
async def test_artifact_error_result_is_recorded_as_workspace_read_error(
    runner, session_state
):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-artifact-error",
        run_id="run-artifact-error",
        session_id="chat-artifact-error",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

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
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "error",
                "data": None,
                "message": "Artifact 'missing' not found.",
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "missing"},
                tool_call_id="tool-call-artifact-error",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error",
        tool_batch_name="read_artifact",
        tool_batch_args=[{"artifact_id": "missing"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    artifact_span = next(span for span in trace.spans if span.name == "read_artifact")
    artifact_event = next(
        event
        for event in trace.events
        if event.event_type == "workspace_read"
        and event.actor.name == "read_artifact"
    )
    assert obs_text == "Artifact 'missing' not found."
    assert tools_results[0]["status"] == "error"
    assert artifact_span.status == "error"
    assert artifact_span.error.message == "Artifact 'missing' not found."
    assert artifact_event.error.message == "Artifact 'missing' not found."


@pytest.mark.asyncio
async def test_artifact_error_result_is_normalized_without_telemetry(
    runner, session_state
):
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
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "error",
                "data": None,
                "message": "Artifact 'missing' not found.",
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "missing"},
                tool_call_id="tool-call-artifact-error",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error-no-telemetry",
        tool_batch_name="read_artifact",
        tool_batch_args=[{"artifact_id": "missing"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert obs_text == "Artifact 'missing' not found."
    assert tools_results[0]["status"] == "error"
    assert tools_results[0]["data"] is None
    assert tools_results[0]["message"] == "Artifact 'missing' not found."


@pytest.mark.asyncio
async def test_artifact_content_starting_with_error_stays_success(
    runner, session_state
):
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
            return {
                "tool_name": tool_name,
                "args": tool_args,
                "status": "success",
                "data": "Error: compiler output from stored artifact",
                "message": None,
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "compiler-log"},
                tool_call_id="tool-call-artifact-error-content",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error-content",
        tool_batch_name="read_artifact",
        tool_batch_args=[{"artifact_id": "compiler-log"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert obs_text == "Error: compiler output from stored artifact"
    assert tools_results[0]["status"] == "success"


@pytest.mark.asyncio
async def test_observation_parser_timeout_uses_tool_timeout_path(session_state):
    runner = ToolBatchRunner(agent_name="test_agent", tool_call_timeout=0.1)
    history = []
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-observation-timeout",
        run_id="run-observation-timeout",
        session_id="chat-observation-timeout",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

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
                content="ok",
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
                "data": "ok",
                "message": None,
            }

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def parse_tool_observation(raw_output):
        await asyncio.sleep(1)
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        raise AssertionError("Timed out observation should not be formatted")

    obs_text, tools_results = await runner.execute(
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="alpha",
                tool_args={},
                tool_call_id="tool-call-alpha",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat-observation-timeout",
        tool_batch_name="alpha",
        tool_batch_args=[{}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace(status="timeout")

    trace = await store.get_trace(context.trace_id)
    observation_span = next(
        span for span in trace.spans if span.kind == "observation.pipeline"
    )
    error_event = next(
        event
        for event in trace.events
        if event.event_type == "observation_pipeline_error"
    )
    assert obs_text == TOOL_CALL_TIMEOUT_MESSAGE
    assert tools_results[0]["status"] == "error"
    assert [item["metadata"]["tool_call_id"] for item in history] == [
        "tool-call-alpha"
    ]
    assert [event.event_type for event in trace.events].count("tool_batch_error") == 1
    assert observation_span.status == "timeout"
    assert error_event.span_id == observation_span.span_id
