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
from omnicoreagent.core.tools.tool_call_resolver import ToolCallResolver
from omnicoreagent.core.tools.tool_action import ToolAction
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.artifact_tools import build_tool_registry_artifact_tool
from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.tools import build_tool_registry_workspace_files
from omnicoreagent.core.types import AgentState, SessionState, ToolCallResult
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector
from omnicoreagent.governance import GovernanceEngine, policy_from_mapping
from omnicoreagent.governance.models import PolicyBudget


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


class CountingExecutor:
    def __init__(self):
        self.calls = 0

    async def execute(
        self,
        agent_name,
        tool_args,
        tool_name,
        tool_call_id,
        add_message_to_history,
        session_id,
    ):
        self.calls += 1
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


def _governance_policy():
    return policy_from_mapping(
        {
            "name": "tool-enforcement",
            "mode": "strict",
            "rules": {
                "deny": [
                    {
                        "rule_id": "deny_local_tools",
                        "capability": "tool.local.call",
                    },
                    {
                        "rule_id": "deny_workspace_write",
                        "capability": "workspace.files.write",
                    },
                ],
                "allow": [
                    {
                        "rule_id": "allow_workspace_read",
                        "capability": "workspace.files.read",
                    }
                ],
            },
        }
    )


def _budget_policy(max_requests: int = 1):
    policy = policy_from_mapping(
        {
            "name": "budget-policy",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "rule_id": "allow_workspace_read",
                        "capability": "workspace.files.read",
                    }
                ]
            },
        }
    )
    policy.budget = PolicyBudget(max_requests=max_requests)
    return policy


def _allow_workspace_policy():
    return policy_from_mapping(
        {
            "name": "workspace-allow",
            "mode": "strict",
            "rules": {
                "allow": [
                    {"rule_id": "allow_workspace", "capability": "workspace.files.*"},
                    {
                        "rule_id": "allow_artifacts",
                        "capability": "workspace.artifacts.*",
                    },
                ]
            },
        }
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
async def test_governance_denies_local_tool_without_executing(session_state):
    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="lookup_customer",
            tool_args={"customer_id": "cus_123"},
            tool_call_id="tool-call-local",
            tool_provider="local",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-local",
        telemetry_recorder=None,
        tool_batch_name="lookup_customer",
        tool_batch_args=[{"customer_id": "cus_123"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Governance denied tool execution" in obs_text
    assert history[0]["metadata"]["governance_error_code"] == "policy_denied"


@pytest.mark.asyncio
async def test_governance_denies_workspace_write_without_executing(session_state):
    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="write_file",
            tool_args={"path": "notes/todo.md", "content": "ship"},
            tool_call_id="tool-call-workspace",
            tool_provider="workspace",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-workspace",
        telemetry_recorder=None,
        tool_batch_name="write_file",
        tool_batch_args=[{"path": "notes/todo.md", "content": "ship"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Governance denied tool execution" in obs_text
    assert history[0]["metadata"]["governance"]["reason_code"] == "matched_deny"


@pytest.mark.asyncio
async def test_governance_allows_workspace_read(session_state):
    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="read_file",
            tool_args={"path": "notes/todo.md"},
            tool_call_id="tool-call-workspace-read",
            tool_provider="workspace",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-workspace-read",
        telemetry_recorder=None,
        tool_batch_name="read_file",
        tool_batch_args=[{"path": "notes/todo.md"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert executor.calls == 1
    assert tools_results[0]["status"] == "success"
    assert obs_text == "read_file:ok"


@pytest.mark.asyncio
async def test_governance_fails_closed_for_mcp_until_phase_three(session_state):
    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="remote_search",
            tool_args={"query": "secret"},
            tool_call_id="tool-call-mcp",
            tool_provider="mcp",
            tool_server="search",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-mcp",
        telemetry_recorder=None,
        tool_batch_name="remote_search",
        tool_batch_args=[{"query": "secret"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "MCP governance is not implemented" in obs_text
    assert history[0]["metadata"]["args"] == "[REDACTED]"
    assert history[0]["metadata"]["governance_error_code"] == "ungoverned_capability"


@pytest.mark.asyncio
async def test_governance_budget_is_atomic_for_parallel_tool_batch(session_state):
    first = CountingExecutor()
    second = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_budget_policy(max_requests=1)),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=first,
            tool_name="read_file",
            tool_args={"path": "notes/one.md"},
            tool_call_id="tool-call-one",
            tool_provider="workspace",
        ),
        ToolCallResult(
            tool_executor=second,
            tool_name="read_file",
            tool_args={"path": "notes/two.md"},
            tool_call_id="tool-call-two",
            tool_provider="workspace",
        ),
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return "\n".join(result["status"] for result in tools_results)

    _obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-budget",
        telemetry_recorder=None,
        tool_batch_name="read_file, read_file",
        tool_batch_args=[{"path": "notes/one.md"}, {"path": "notes/two.md"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    statuses = [result["status"] for result in tools_results]
    assert statuses.count("success") == 1
    assert statuses.count("error") == 1
    assert first.calls + second.calls == 1


@pytest.mark.asyncio
async def test_governance_denial_redacts_args_in_history_and_result(session_state):
    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="lookup_customer",
            tool_args={"api_key": "secret", "customer_id": "cus_123"},
            tool_call_id="tool-call-redact",
            tool_provider="local",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    _obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-redact",
        telemetry_recorder=None,
        tool_batch_name="lookup_customer",
        tool_batch_args=[{"api_key": "secret", "customer_id": "cus_123"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert executor.calls == 0
    assert tools_results[0]["args"] == {}
    assert history[0]["metadata"]["args"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_governed_batch_execution_error_redacts_args(
    session_state,
):
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_allow_workspace_policy()),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=CountingExecutor(),
            tool_name="read_file",
            tool_args={"path": "customers/cus_123/private.md"},
            tool_call_id="tool-call-timeout-redact",
            tool_provider="workspace",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    results = await runner.handle_execution_error(
        tool_call_results=tool_calls,
        error_message="timeout",
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-timeout",
        tool_batch_name="read_file",
    )

    assert results[0]["args"] == {"path": "[REDACTED]"}
    assert history[0]["metadata"]["args"] == {"path": "[REDACTED]"}


@pytest.mark.asyncio
async def test_governance_denial_emits_policy_telemetry_without_tool_args(
    session_state,
):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(
        trace_id="trace-governance-denial",
        session_id="governed-telemetry",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(
            _governance_policy(),
            telemetry_recorder=recorder,
        ),
    )
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="lookup_customer",
            tool_args={"api_key": "secret", "customer_id": "cus_123"},
            tool_call_id="tool-call-telemetry-redact",
            tool_provider="local",
        )
    ]

    history = []

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-telemetry",
        telemetry_recorder=recorder,
        tool_batch_name="lookup_customer",
        tool_batch_args=[{"api_key": "secret", "customer_id": "cus_123"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )
    await recorder.end_trace()

    trace = await store.get_trace("trace-governance-denial")
    assert trace is not None
    event_types = [event.event_type for event in trace.events]
    assert "policy_request_created" in event_types
    assert "policy_decision_deny" in event_types
    assert "tool_call" not in event_types
    request_event = next(
        event for event in trace.events if event.event_type == "policy_request_created"
    )
    assert "api_key" not in str(request_event.input)
    assert "secret" not in str(request_event.input)


@pytest.mark.asyncio
async def test_governed_successful_tool_telemetry_redacts_result_args(session_state):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    await recorder.start_trace(
        trace_id="trace-governance-success",
        session_id="governed-success-telemetry",
        actor=TelemetryActor(type=ActorType.AGENT, name="test_agent"),
    )

    executor = CountingExecutor()
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="read_file",
            tool_args={"path": "customers/cus_123/private.md"},
            tool_call_id="tool-call-success-redact",
            tool_provider="workspace",
        )
    ]

    history = []

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    _obs_text, tools_results = await runner.execute(
        tool_call_results=tool_calls,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-success-telemetry",
        telemetry_recorder=recorder,
        tool_batch_name="read_file",
        tool_batch_args=[{"path": "customers/cus_123/private.md"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )
    await recorder.end_trace()

    assert tools_results[0]["args"] == "[REDACTED]"
    assert history[0]["metadata"]["args"] == "[REDACTED]"
    trace = await store.get_trace("trace-governance-success")
    assert trace is not None
    workspace_event = next(
        event for event in trace.events if event.event_type == "workspace_read"
    )
    assert workspace_event.output["args"] == "[REDACTED]"
    read_span = next(span for span in trace.spans if span.name == "read_file")
    assert read_span.output["args"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_governed_start_redacts_assistant_tool_call_content(session_state):
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )
    history = []
    response = """
<tool_call>
  <tool_name>lookup_customer</tool_name>
  <parameters><api_key>secret</api_key></parameters>
</tool_call>
"""
    tool_calls = [
        ToolCallResult(
            tool_executor=CountingExecutor(),
            tool_name="lookup_customer",
            tool_args={"api_key": "secret"},
            tool_call_id="tool-call-content-redact",
            tool_provider="local",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    await runner.start(
        tool_call_results=tool_calls,
        response=response,
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="governed-content-redact",
    )

    assert "secret" not in history[0]["content"]
    assert "secret" not in session_state.messages[-1].content
    assert "lookup_customer" in history[0]["content"]
    tool_args = history[0]["metadata"]["tool_calls"][0]["function"]["arguments"]
    assert "secret" not in tool_args


@pytest.mark.asyncio
async def test_governance_denied_workspace_write_does_not_touch_real_storage(
    session_state,
    tmp_path,
):
    registry = ToolRegistry()
    workspace_dir = tmp_path / "workspace"
    build_tool_registry_workspace_files(
        registry=registry,
        workspace_config=WorkspaceConfig(workspace_dir=workspace_dir),
    )
    resolved = await ToolCallResolver().resolve_single_action(
        action=ToolAction(
            tool_name="write_file",
            parameters={"path": "notes/denied.md", "content": "blocked"},
            raw={
                "tool": "write_file",
                "parameters": {"path": "notes/denied.md", "content": "blocked"},
            },
        ),
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_governance_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["message"]

    _obs_text, tools_results = await runner.execute(
        tool_call_results=[resolved],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="real-workspace-deny",
        telemetry_recorder=None,
        tool_batch_name="write_file",
        tool_batch_args=[{"path": "notes/denied.md", "content": "blocked"}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert tools_results[0]["status"] == "error"
    assert not (workspace_dir / "files" / "notes" / "denied.md").exists()


@pytest.mark.asyncio
async def test_governance_allows_real_workspace_write_and_read(
    session_state,
    tmp_path,
):
    registry = ToolRegistry()
    workspace_dir = tmp_path / "workspace"
    build_tool_registry_workspace_files(
        registry=registry,
        workspace_config=WorkspaceConfig(workspace_dir=workspace_dir),
    )
    resolved = await ToolCallResolver().resolve_single_action(
        action=ToolAction(
            tool_name="write_file",
            parameters={
                "path": "notes/allowed.md",
                "content": "allowed",
                "mode": "create",
            },
            raw={
                "tool": "write_file",
                "parameters": {"path": "notes/allowed.md", "content": "allowed"},
            },
        ),
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_allow_workspace_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    _obs_text, tools_results = await runner.execute(
        tool_call_results=[resolved],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="real-workspace-allow",
        telemetry_recorder=None,
        tool_batch_name="write_file",
        tool_batch_args=[
            {"path": "notes/allowed.md", "content": "allowed", "mode": "create"}
        ],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert tools_results[0]["status"] == "success"
    assert (workspace_dir / "files" / "notes" / "allowed.md").read_text() == "allowed"


@pytest.mark.asyncio
async def test_governance_controls_real_artifact_tool_execution(session_state, tmp_path):
    offloader = ToolResponseOffloader(
        config={"enabled": True},
        base_dir=str(tmp_path / "artifacts"),
    )
    artifact = offloader.offload("search", "artifact content")
    registry = ToolRegistry()
    build_tool_registry_artifact_tool(offloader, registry)
    resolved = await ToolCallResolver().resolve_single_action(
        action=ToolAction(
            tool_name="read_artifact",
            parameters={"artifact_id": artifact.artifact_id},
            raw={
                "tool": "read_artifact",
                "parameters": {"artifact_id": artifact.artifact_id},
            },
        ),
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )
    runner = ToolBatchRunner(
        agent_name="test_agent",
        tool_call_timeout=10,
        governance_engine=GovernanceEngine(_allow_workspace_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    async def parse_tool_observation(raw_output):
        return raw_output

    def build_tool_results_observation(
        tool_call_results, tools_results, session_state, session_id
    ):
        return tools_results[0]["data"]

    _obs_text, tools_results = await runner.execute(
        tool_call_results=[resolved],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="artifact-allow",
        telemetry_recorder=None,
        tool_batch_name="read_artifact",
        tool_batch_args=[{"artifact_id": artifact.artifact_id}],
        parse_tool_observation=parse_tool_observation,
        build_tool_results_observation=build_tool_results_observation,
    )

    assert tools_results[0]["status"] == "success"
    assert tools_results[0]["data"] == "artifact content"


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
            tool_name="read_file",
            tool_args={"path": "notes.md"},
            tool_call_id="tool-call-workspace",
            tool_provider="workspace",
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
        tool_batch_name="search_docs, read_file",
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
        "read_file",
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
                tool_provider="artifact",
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
                tool_name="read_file",
                tool_args={"path": "notes.md"},
                tool_call_id="tool-call-workspace-redacted",
                tool_provider="workspace",
            )
        ],
        session_state=session_state,
        add_message_to_history=add_message_to_history,
        session_id="chat801",
        tool_batch_name="read_file",
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
                tool_provider="artifact",
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
                tool_provider="artifact",
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
                tool_provider="artifact",
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
