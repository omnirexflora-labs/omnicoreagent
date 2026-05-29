import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    NetworkPolicy,
    NoneSandboxRuntime,
    SandboxEnvironment,
    SandboxLifecycle,
    SandboxLifecycleCleanup,
    SandboxAuthorityContext,
    SandboxCommandContext,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxProvider,
    SandboxResources,
    SandboxSessionNotFoundError,
    SandboxUnsupportedError,
    WorkspaceMount,
)
from omnicoreagent.sandbox.telemetry import emit_sandbox_event


@pytest.mark.asyncio
async def test_none_sandbox_is_policy_only_and_cannot_execute():
    runtime = NoneSandboxRuntime()
    session = await runtime.create(SandboxManifest())

    assert session.provider == SandboxProvider.NONE
    assert runtime.supports_required_sandbox is False
    with pytest.raises(SandboxUnsupportedError):
        await runtime.execute(
            session.session_id,
            SandboxExecRequest(command=["echo"], authority=_authority()),
        )


@pytest.mark.asyncio
async def test_local_test_sandbox_executes_registered_command_and_shapes_observation():
    runtime = LocalTestSandboxRuntime(
        commands={
            "echo": lambda request: SandboxExecResult(
                exit_code=0,
                stdout=" ".join(request.command[1:]),
                metadata={"handled_by": "test"},
            )
        }
    )
    session = await runtime.create(
        SandboxManifest(
            workspace_mount=WorkspaceMount(
                source="/workspace/project",
                target="/workspace",
                mode="read_write",
            )
        )
    )

    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(
            command=["echo", "hello", "sandbox"],
            authority=_authority(),
        ),
    )

    assert runtime.supports_required_sandbox is True
    assert result.ok is True
    assert result.to_observation() == {
        "status": "success",
        "exit_code": 0,
        "stdout": "hello sandbox",
        "stderr": "",
        "timed_out": False,
        "metadata": {
            "handled_by": "test",
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
    }


def test_sandbox_observation_truncates_untrusted_output():
    result = SandboxExecResult(exit_code=0, stdout="abcdef", stderr="uvwxyz")

    assert result.to_observation(max_output_chars=3) == {
        "status": "success",
        "exit_code": 0,
        "stdout": "abc",
        "stderr": "uvw",
        "timed_out": False,
        "metadata": {"stdout_truncated": True, "stderr_truncated": True},
    }


@pytest.mark.asyncio
async def test_local_test_sandbox_files_network_snapshot_and_terminate():
    runtime = LocalTestSandboxRuntime()
    session = await runtime.create(SandboxManifest())

    await runtime.write_file(session.session_id, "/workspace/out.txt", b"done")
    await runtime.set_network_policy(
        session.session_id,
        NetworkPolicy(default="deny", allowed_hosts=["api.example.com"]),
    )
    snapshot = await runtime.snapshot(session.session_id)

    assert await runtime.read_file(session.session_id, "/workspace/out.txt") == b"done"
    assert runtime.network_policies[session.session_id].allowed_hosts == [
        "api.example.com"
    ]
    assert snapshot.session_id == session.session_id

    await runtime.terminate(session.session_id)
    with pytest.raises(SandboxSessionNotFoundError):
        await runtime.read_file(session.session_id, "/workspace/out.txt")


@pytest.mark.asyncio
async def test_local_test_sandbox_enforces_mount_scope_and_read_only_mode():
    runtime = LocalTestSandboxRuntime()
    session = await runtime.create(
        SandboxManifest(
            workspace_mount=WorkspaceMount(
                source="/tmp/project",
                target="/workspace",
                mode="read_only",
            )
        )
    )

    with pytest.raises(SandboxUnsupportedError, match="read-only"):
        await runtime.write_file(session.session_id, "/workspace/out.txt", b"blocked")
    with pytest.raises(SandboxUnsupportedError, match="escapes"):
        await runtime.read_file(session.session_id, "/tmp/out.txt")
    with pytest.raises(SandboxUnsupportedError, match="escapes"):
        await runtime.read_file(session.session_id, "/workspace/../outside.txt")


def test_sandbox_nested_models_are_top_level_exports():
    assert SandboxEnvironment().plain == {}
    assert SandboxResources().gpu is False
    assert SandboxLifecycle(cleanup="always").cleanup == SandboxLifecycleCleanup.ALWAYS


@pytest.mark.asyncio
async def test_local_test_sandbox_passes_execution_context_to_handlers():
    captured = {}

    def handler(request: SandboxExecRequest, context: SandboxCommandContext):
        captured["allowed_hosts"] = context.network_policy.allowed_hosts
        captured["provider"] = context.session.provider.value
        return SandboxExecResult(exit_code=0, stdout=request.command[0])

    runtime = LocalTestSandboxRuntime(commands={"inspect": handler})
    session = await runtime.create(SandboxManifest())
    await runtime.set_network_policy(
        session.session_id,
        NetworkPolicy(default="deny", allowed_hosts=["api.example.com"]),
    )

    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=["inspect"], authority=_authority()),
    )

    assert result.stdout == "inspect"
    assert captured == {"allowed_hosts": ["api.example.com"], "provider": "local_test"}


@pytest.mark.asyncio
async def test_local_test_sandbox_emits_telemetry_events():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-sandbox",
        run_id="run-sandbox",
        session_id="session-sandbox",
        actor=TelemetryActor(type=ActorType.SYSTEM, name="sandbox-test"),
    )
    runtime = LocalTestSandboxRuntime(
        telemetry_recorder=recorder,
        commands={"ok": lambda request: SandboxExecResult(exit_code=0, stdout="ok")},
    )

    session = await runtime.create(SandboxManifest())
    await runtime.execute(
        session.session_id,
        SandboxExecRequest(
            command=["ok"],
            authority=_authority(),
        ),
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert [event.event_type for event in trace.events] == [
        "sandbox_session_created",
        "sandbox_exec_started",
        "sandbox_exec_completed",
    ]
    assert trace.events[1].input["command"] == {"name": "ok", "argc": 1}
    assert trace.events[-1].output["observation_summary"]["stdout_chars"] == 2
    assert trace.events[-1].output["authority"]["decision_id"] == "decision_1"


@pytest.mark.asyncio
async def test_sandbox_telemetry_strict_mode_reraises_missing_trace_errors():
    class MissingTraceRecorder:
        config = TelemetryConfig(strict=True)

        async def emit_event(self, *args, **kwargs):
            raise RuntimeError("no active trace")

    with pytest.raises(RuntimeError, match="no active trace"):
        await emit_sandbox_event(MissingTraceRecorder(), "sandbox_exec_started")


def _authority() -> SandboxAuthorityContext:
    return SandboxAuthorityContext(
        authority_request_id="authreq_1",
        decision_id="decision_1",
        policy_id="policy_1",
        policy_hash="hash_1",
        matched_rule_ids=["allow_sandbox"],
        reason_code="matched_allow",
    )
