"""Telemetry for code execution: every session and command is in the trace.

The governed service records them, so every sandbox provider is recorded the
same way. Facts (session, provider, exit code, duration, sizes, the matched
policy rules) are metadata and kept under every capture policy; the command and
its output are payloads and follow the capture policy for tool results.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.telemetry.redaction import TelemetryConfig
from omnicoreagent.governance import GovernanceEngine, build_default_policy
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    SandboxCommandSpec,
    SandboxExecResult,
    SandboxExecutionService,
)

SANDBOX_EVENTS = {
    "sandbox_session_created",
    "sandbox_exec_started",
    "sandbox_exec_completed",
    "sandbox_exec_failed",
    "sandbox_session_closed",
    "sandbox_workspace_sync",
}


def _boom(request):
    raise RuntimeError("the provider went away")


async def _recorded(config: TelemetryConfig, run):
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, config)
    context = await recorder.start_trace(
        trace_id="trace-exec",
        run_id="run-exec",
        actor=TelemetryActor(type=ActorType.SYSTEM, name="execution-test"),
    )
    runtime = LocalTestSandboxRuntime(
        commands={
            "echo": lambda request: SandboxExecResult(exit_code=0, stdout="secret output"),
            "fail": lambda request: SandboxExecResult(exit_code=3, stderr="bad"),
            "boom": _boom,
        }
    )
    engine = GovernanceEngine(
        build_default_policy("interactive-dev"),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
        telemetry_recorder=recorder,
    )
    await run(SandboxExecutionService(engine))
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    return [e for e in trace.events if e.event_type in SANDBOX_EVENTS]


async def _session_with_three_commands(service):
    session = await service.open_session()
    await service.execute(SandboxCommandSpec(command=["echo", "hi"], timeout_seconds=5), session=session)
    await service.execute(SandboxCommandSpec(command=["fail"]), session=session)
    with pytest.raises(RuntimeError):
        await service.execute(SandboxCommandSpec(command=["boom"]), session=session)
    await service.close_session(session)


@pytest.mark.asyncio
async def test_a_session_and_each_command_are_recorded_with_their_facts():
    events = await _recorded(TelemetryConfig(capture="full"), _session_with_three_commands)

    assert [e.event_type for e in events] == [
        "sandbox_session_created",
        "sandbox_exec_started",
        "sandbox_exec_completed",
        "sandbox_exec_started",
        "sandbox_exec_failed",
        "sandbox_exec_started",
        "sandbox_exec_failed",
        "sandbox_session_closed",
    ]
    session_id = events[0].metadata["sandbox_session_id"]
    assert events[0].metadata["sandbox_provider"] == "local_test"
    assert all(e.metadata["sandbox_session_id"] == session_id for e in events)

    started, done = events[1], events[2]
    assert started.metadata["command_name"] == "echo"
    assert done.metadata["execution_id"] == started.metadata["execution_id"]
    assert done.metadata["exit_code"] == 0 and done.metadata["timed_out"] is False
    assert done.metadata["stdout_bytes"] == len("secret output")
    assert done.metadata["matched_rule_ids"] == ["allow_sandboxed_execution"]
    assert isinstance(done.metadata["duration_ms"], (int, float))
    assert done.input["command"] == ["echo", "hi"]
    assert done.output["stdout"] == "secret output"

    assert events[4].metadata["exit_code"] == 3
    crashed = events[6]
    assert crashed.error is not None and "went away" in crashed.error.message
    assert events[-1].metadata["commands"] == 3


@pytest.mark.asyncio
async def test_command_and_output_follow_the_capture_policy_but_facts_stay():
    config = TelemetryConfig(capture="default", record_tool_results=False)
    events = await _recorded(config, _session_with_three_commands)

    done = next(e for e in events if e.event_type == "sandbox_exec_completed")
    assert done.metadata["exit_code"] == 0
    assert "secret output" not in json.dumps(done.model_dump(), default=str)


def test_sandbox_event_types_are_registered():
    from omnicoreagent.core.telemetry.models import FOUNDATION_EVENT_TYPES

    assert SANDBOX_EVENTS <= FOUNDATION_EVENT_TYPES


@pytest.mark.asyncio
async def test_the_trajectory_shows_each_execution_under_its_tool_call_and_totals_count_them(tmp_path):
    from test_execute_tool import ScriptedModel, _agent, needs_docker  # noqa: F401

    if needs_docker.args[0]:
        pytest.skip(needs_docker.kwargs["reason"])
    from omnicoreagent.core.telemetry.trajectory import build_trajectory

    model = ScriptedModel(
        [("c1", "execute", json.dumps({"command": "echo 7 > seven.txt && echo made"}))],
        [("c2", "execute", json.dumps({"command": "exit 2"}))],
        "done",
    )
    agent = await _agent(
        model,
        sandbox=True,
        enable_workspace_files=True,
        workspace_config={"workspace_dir": str(tmp_path / "ws")},
    )

    result = await agent.run("go", session_id="exec-telemetry")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    trajectory = build_trajectory(trace)

    calls = [call for step in trajectory["steps"] for call in step["tool_calls"]]
    first, second = calls
    assert [x["exit_code"] for x in first["executions"]] == [0]
    assert first["executions"][0]["command"] == ["sh", "-c", "echo 7 > seven.txt && echo made"]
    assert first["executions"][0]["stdout"] == "made\n"
    assert first["workspace_sync"]["written"] == ["seven.txt"]
    assert [x["exit_code"] for x in second["executions"]] == [2]

    totals = trajectory["totals"]["executions"]
    assert totals == {"sessions": 1, "commands": 2, "failed": 1, "timed_out": 0}
    assert {"path": "seven.txt", "operation": "write", "via": "sandbox"} in [
        {k: change.get(k) for k in ("path", "operation", "via")}
        for change in trajectory["totals"]["workspace_changes"]
    ]
    # The session lifecycle is in the trajectory too, not dropped.
    lifecycle = [e["event_type"] for e in trajectory["other_events"]]
    assert "sandbox_session_closed" in lifecycle
