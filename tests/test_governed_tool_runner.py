import pytest
import asyncio
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.governed_tool_runner import GovernedToolRunner
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.artifact_tools import (
    build_tool_registry_artifact_tool,
)
from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.tools import build_tool_registry_workspace_files
from omnicoreagent.core.types import AgentState, SessionState, ToolCallResult
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector
from omnicoreagent.governance import (
    GovernanceEngine,
    build_default_policy,
    policy_from_mapping,
)
from omnicoreagent.governance.models import PolicyBudget


@pytest.fixture
def runner():
    return GovernedToolRunner(agent_name="test_agent")


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
                    {"rule_id": "deny_local_tools", "capability": "tool.local.call"},
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


def _mcp_policy():
    return policy_from_mapping(
        {
            "name": "mcp-policy",
            "mode": "strict",
            "rules": {
                "deny": [
                    {
                        "rule_id": "deny_destructive_docs",
                        "capability": "tool.mcp.call",
                        "target": {
                            "mcp_server": "docs-server",
                            "tool_name": "delete_docs",
                        },
                    }
                ],
                "allow": [
                    {
                        "rule_id": "allow_docs_search",
                        "capability": "tool.mcp.call",
                        "target": {
                            "mcp_server": "docs-server",
                            "tool_name": "search_docs",
                        },
                    }
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_governance_denies_local_tool_without_executing(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-local",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Governance denied tool execution" in tools_results[0].get("message", "")
    assert history[0]["metadata"]["governance_error_code"] == "policy_denied"


@pytest.mark.asyncio
async def test_governance_denies_workspace_write_without_executing(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-workspace",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Governance denied tool execution" in tools_results[0].get("message", "")
    assert history[0]["metadata"]["governance"]["reason_code"] == "matched_deny"


@pytest.mark.asyncio
async def test_governance_allows_workspace_read(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-workspace-read",
        telemetry_recorder=None,
    )
    assert executor.calls == 1
    assert tools_results[0]["status"] == "success"
    assert tools_results[0]["data"] == "read_file:ok"


@pytest.mark.asyncio
async def test_governance_requires_policy_for_mcp_tool(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-mcp",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Unknown capability denied" in tools_results[0].get("message", "")
    assert history[0]["metadata"]["args"] == "[REDACTED]"
    assert history[0]["metadata"]["governance_error_code"] == "unknown_capability"


@pytest.mark.asyncio
async def test_governance_interactive_default_requires_mcp_approval(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
        governance_engine=GovernanceEngine(build_default_policy("interactive-dev")),
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="remote_search",
            tool_args={"query": "docs"},
            tool_call_id="tool-call-mcp-approval",
            tool_provider="mcp",
            tool_server="search",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-mcp-approval",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Matched ask policy rule" in tools_results[0].get("message", "")
    assert history[0]["metadata"]["governance_error_code"] == "approval_required"


@pytest.mark.asyncio
async def test_governance_allows_specific_mcp_tool(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent", governance_engine=GovernanceEngine(_mcp_policy())
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="search_docs",
            tool_args={"query": "governance"},
            tool_call_id="tool-call-mcp-allow",
            tool_provider="mcp",
            tool_server="docs-server",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-mcp-allow",
        telemetry_recorder=None,
    )
    assert executor.calls == 1
    assert tools_results[0]["status"] == "success"
    assert tools_results[0]["args"] == "[REDACTED]"
    assert history[0]["metadata"]["args"] == "[REDACTED]"
    assert tools_results[0]["data"] == "search_docs:ok"


@pytest.mark.asyncio
async def test_governance_denies_specific_mcp_tool_on_allowed_server(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent", governance_engine=GovernanceEngine(_mcp_policy())
    )
    history = []
    tool_calls = [
        ToolCallResult(
            tool_executor=executor,
            tool_name="delete_docs",
            tool_args={"path": "prod"},
            tool_call_id="tool-call-mcp-deny",
            tool_provider="mcp",
            tool_server="docs-server",
        )
    ]

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append({"role": role, "content": content, "metadata": metadata or {}})

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-mcp-deny",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["status"] == "error"
    assert "Governance denied tool execution" in tools_results[0].get("message", "")
    assert history[0]["metadata"]["governance_error_code"] == "policy_denied"


@pytest.mark.asyncio
async def test_governance_budget_is_atomic_for_parallel_tool_batch(session_state):
    first = CountingExecutor()
    second = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-budget",
        telemetry_recorder=None,
    )
    statuses = [result["status"] for result in tools_results]
    assert statuses.count("success") == 1
    assert statuses.count("error") == 1
    assert first.calls + second.calls == 1


@pytest.mark.asyncio
async def test_governance_denial_redacts_args_in_history_and_result(session_state):
    executor = CountingExecutor()
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-redact",
        telemetry_recorder=None,
    )
    assert executor.calls == 0
    assert tools_results[0]["args"] == {}
    assert history[0]["metadata"]["args"] == "[REDACTED]"


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
    runner = GovernedToolRunner(
        agent_name="test_agent",
        governance_engine=GovernanceEngine(
            _governance_policy(), telemetry_recorder=recorder
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

    await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-telemetry",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace("trace-governance-denial")
    assert trace is not None
    event_types = [event.event_type for event in trace.events]
    assert "policy_request_created" in event_types
    assert "policy_decision_deny" in event_types
    assert "tool_call" not in event_types
    request_event = next(
        (
            event
            for event in trace.events
            if event.event_type == "policy_request_created"
        )
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
    runner = GovernedToolRunner(
        agent_name="test_agent",
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=tool_calls,
        add_message_to_history=add_message_to_history,
        session_id="governed-success-telemetry",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    assert tools_results[0]["args"] == "[REDACTED]"
    assert history[0]["metadata"]["args"] == "[REDACTED]"
    trace = await store.get_trace("trace-governance-success")
    assert trace is not None
    workspace_event = next(
        (event for event in trace.events if event.event_type == "workspace_read")
    )
    assert workspace_event.output["args"] == "[REDACTED]"
    read_span = next((span for span in trace.spans if span.name == "read_file"))
    assert read_span.output["args"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_governance_denied_workspace_write_does_not_touch_real_storage(
    session_state, tmp_path
):
    registry = ToolRegistry()
    workspace_dir = tmp_path / "workspace"
    build_tool_registry_workspace_files(
        registry=registry, workspace_config=WorkspaceConfig(workspace_dir=workspace_dir)
    )
    resolved = resolve_local(
        registry, "write_file", {"path": "notes/denied.md", "content": "blocked"}
    )
    runner = GovernedToolRunner(
        agent_name="test_agent",
        governance_engine=GovernanceEngine(_governance_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    tools_results = await execute_calls(
        runner,
        tool_call_results=[resolved],
        add_message_to_history=add_message_to_history,
        session_id="real-workspace-deny",
        telemetry_recorder=None,
    )
    assert tools_results[0]["status"] == "error"
    assert not (workspace_dir / "files" / "notes" / "denied.md").exists()


@pytest.mark.asyncio
async def test_governance_allows_real_workspace_write_and_read(session_state, tmp_path):
    registry = ToolRegistry()
    workspace_dir = tmp_path / "workspace"
    build_tool_registry_workspace_files(
        registry=registry, workspace_config=WorkspaceConfig(workspace_dir=workspace_dir)
    )
    resolved = resolve_local(
        registry,
        "write_file",
        {"path": "notes/allowed.md", "content": "allowed", "mode": "create"},
    )
    runner = GovernedToolRunner(
        agent_name="test_agent",
        governance_engine=GovernanceEngine(_allow_workspace_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    tools_results = await execute_calls(
        runner,
        tool_call_results=[resolved],
        add_message_to_history=add_message_to_history,
        session_id="real-workspace-allow",
        telemetry_recorder=None,
    )
    assert tools_results[0]["status"] == "success"
    assert (workspace_dir / "files" / "notes" / "allowed.md").read_text() == "allowed"


@pytest.mark.asyncio
async def test_governance_controls_real_artifact_tool_execution(
    session_state, tmp_path
):
    offloader = ToolResponseOffloader(
        config={"enabled": True}, base_dir=str(tmp_path / "artifacts")
    )
    artifact = offloader.offload("search", "artifact content")
    registry = ToolRegistry()
    build_tool_registry_artifact_tool(offloader, registry)
    resolved = resolve_local(
        registry, "read_artifact", {"artifact_id": artifact.artifact_id}
    )
    runner = GovernedToolRunner(
        agent_name="test_agent",
        governance_engine=GovernanceEngine(_allow_workspace_policy()),
    )

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        return None

    tools_results = await execute_calls(
        runner,
        tool_call_results=[resolved],
        add_message_to_history=add_message_to_history,
        session_id="artifact-allow",
        telemetry_recorder=None,
    )
    assert tools_results[0]["status"] == "success"
    assert tools_results[0]["data"] == "artifact content"


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

    await execute_calls(
        runner,
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "search_123"},
                tool_call_id="tool-call-artifact",
                tool_provider="artifact",
            )
        ],
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    artifact_span = next((span for span in trace.spans if span.name == "read_artifact"))
    artifact_event = next(
        (
            event
            for event in trace.events
            if event.event_type == "workspace_read"
            and event.actor.name == "read_artifact"
        )
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

    await execute_calls(
        runner,
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_file",
                tool_args={"path": "notes.md"},
                tool_call_id="tool-call-workspace-redacted",
                tool_provider="workspace",
            )
        ],
        add_message_to_history=add_message_to_history,
        session_id="chat801",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    workspace_span = next(
        (span for span in trace.spans if span.kind == "workspace.read")
    )
    workspace_event = next(
        (event for event in trace.events if event.event_type == "workspace_read")
    )
    assert workspace_span.output is None
    assert workspace_event.output is None


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

    tools_results = await execute_calls(
        runner,
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "missing"},
                tool_call_id="tool-call-artifact-error",
                tool_provider="artifact",
            )
        ],
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    artifact_span = next((span for span in trace.spans if span.name == "read_artifact"))
    artifact_event = next(
        (
            event
            for event in trace.events
            if event.event_type == "workspace_read"
            and event.actor.name == "read_artifact"
        )
    )
    assert tools_results[0].get("message", "") == "Artifact 'missing' not found."
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "missing"},
                tool_call_id="tool-call-artifact-error",
                tool_provider="artifact",
            )
        ],
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error-no-telemetry",
    )
    assert tools_results[0].get("message", "") == "Artifact 'missing' not found."
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

    tools_results = await execute_calls(
        runner,
        tool_call_results=[
            ToolCallResult(
                tool_executor=FakeExecutor(),
                tool_name="read_artifact",
                tool_args={"artifact_id": "compiler-log"},
                tool_call_id="tool-call-artifact-error-content",
                tool_provider="artifact",
            )
        ],
        add_message_to_history=add_message_to_history,
        session_id="chat-artifact-error-content",
    )
    assert tools_results[0]["data"] == "Error: compiler output from stored artifact"
    assert tools_results[0]["status"] == "success"


async def execute_calls(
    runner,
    *,
    tool_call_results,
    add_message_to_history,
    session_id,
    telemetry_recorder=None,
):
    return await asyncio.gather(
        *[
            runner.execute(
                single_tool=call,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                telemetry_recorder=telemetry_recorder,
            )
            for call in tool_call_results
        ]
    )


def resolve_local(registry, name, arguments):
    import json
    from omnicoreagent.core.model_protocol import ToolRequest
    from omnicoreagent.core.tools.native_catalog import NativeToolCatalog
    from omnicoreagent.core.tools.tool_executor import ToolExecutor
    from omnicoreagent.core.tools.local_tool_handler import LocalToolHandler

    binding, args = NativeToolCatalog(local_tools=registry).resolve(
        ToolRequest("test-call", name, json.dumps(arguments))
    )
    return ToolCallResult(
        ToolExecutor(LocalToolHandler(registry)),
        binding.name,
        args,
        "test-call",
        binding.provider,
        binding.server,
    )
