import pytest

import omnicoreagent
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.governance import (
    AuthorityRequest,
    GovernanceEngine,
    PolicyConstraints,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    PolicyRuleSet,
    SandboxRequiredError,
    UnknownCapabilityError,
)
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    NetworkPolicy,
    NoneSandboxRuntime,
    SandboxEnvironment,
    SandboxLifecycle,
    SandboxLifecycleCleanup,
    SandboxAuthorityContext,
    SandboxCommandSpec,
    SandboxCommandContext,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxExecutionService,
    SandboxFilesystemDefault,
    SandboxFilesystemPolicy,
    SandboxManifest,
    SandboxNetworkDefault,
    SandboxProvider,
    SandboxResources,
    SandboxRuntimeConfig,
    SandboxSessionNotFoundError,
    SandboxUnsupportedError,
    WorkspaceMount,
    build_sandbox_runtime,
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


def test_build_sandbox_runtime_from_config():
    none_runtime = build_sandbox_runtime({"provider": "none"})
    local_runtime = build_sandbox_runtime(SandboxRuntimeConfig(provider="local_test"))

    assert isinstance(none_runtime, NoneSandboxRuntime)
    assert isinstance(local_runtime, LocalTestSandboxRuntime)
    assert build_sandbox_runtime(local_runtime) is local_runtime

    with pytest.raises(ValueError, match="Unsupported sandbox provider"):
        build_sandbox_runtime({"provider": "docker"})


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
    assert omnicoreagent.SandboxExecutionService is SandboxExecutionService
    assert omnicoreagent.SandboxCommandSpec is SandboxCommandSpec


def test_sandbox_manifest_normalizes_policy_contracts():
    manifest = SandboxManifest(
        working_dir="/workspace/./app",
        workspace_mount={
            "source": "s3://bucket/workspaces/agent-a",
            "target": "/workspace",
            "mode": "read_write",
        },
        filesystem_policy={
            "default": "deny",
            "readable_paths": ["/workspace/./input"],
            "writable_paths": ["/workspace/out"],
        },
        network_policy={
            "default": "deny",
            "allowed_hosts": ["API.EXAMPLE.COM", "*.Internal.EXAMPLE"],
            "denied_hosts": ["blocked.example.com"],
        },
        environment={
            "plain": {"APP_ENV": "test"},
            "secret_refs": ["stripe/live"],
        },
    )

    assert manifest.working_dir == "/workspace/app"
    assert manifest.workspace_mount.source == "s3://bucket/workspaces/agent-a"
    assert manifest.filesystem_policy.default == SandboxFilesystemDefault.DENY
    assert manifest.filesystem_policy.readable_paths == ["/workspace/input"]
    assert manifest.network_policy.default == SandboxNetworkDefault.DENY
    assert manifest.network_policy.allowed_hosts == [
        "api.example.com",
        "*.internal.example",
    ]
    assert manifest.environment.plain == {"APP_ENV": "test"}
    assert manifest.environment.secret_refs == ["stripe/live"]


def test_sandbox_policy_contracts_reject_unsafe_values():
    with pytest.raises(ValueError, match="escapes"):
        SandboxFilesystemPolicy(readable_paths=["/workspace/../host"])
    with pytest.raises(ValueError, match="escapes"):
        SandboxFilesystemPolicy(readable_paths=["/workspace/%2e%2e/host"])
    with pytest.raises(ValueError, match="escapes"):
        SandboxFilesystemPolicy(writable_paths=["/workspace/%2e%2e/host"])
    with pytest.raises(ValueError, match="escapes"):
        SandboxFilesystemPolicy(denied_paths=["/workspace/%2e%2e/host"])
    with pytest.raises(ValueError, match="escapes"):
        SandboxManifest(working_dir="/workspace/%2e%2e/host")
    with pytest.raises(ValueError, match="mount source"):
        WorkspaceMount(source="../host", target="/workspace")
    with pytest.raises(ValueError, match="escapes"):
        WorkspaceMount(source="/workspace/%2e%2e/host", target="/workspace")
    with pytest.raises(ValueError, match="escapes"):
        WorkspaceMount(source="/workspace", target="/workspace/%2e%2e/host")
    with pytest.raises(ValueError, match="credentials"):
        WorkspaceMount(source="s3://user:secret@bucket/workspace", target="/workspace")
    with pytest.raises(ValueError, match="host pattern"):
        NetworkPolicy(allowed_hosts=["https://api.example.com/path"])
    with pytest.raises(ValueError, match="port"):
        NetworkPolicy(allowed_hosts=["api.example.com:443"])
    with pytest.raises(ValueError, match="environment variable"):
        SandboxEnvironment(plain={"1INVALID": "value"})
    with pytest.raises(ValueError, match="secret ref"):
        SandboxEnvironment(secret_refs=["../root"])


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
async def test_sandbox_execution_service_authorizes_executes_and_cleans_up():
    runtime = LocalTestSandboxRuntime(
        commands={
            "ok": lambda request: SandboxExecResult(
                exit_code=0,
                stdout="done",
                metadata={"handled": True},
            )
        }
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    result = await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(command=["ok", "--fast"])
    )

    assert result.ok is True
    assert result.stdout == "done"
    assert runtime.sessions == {}
    assert result.metadata["handled"] is True
    assert result.metadata["sandbox_provider"] == "local_test"
    assert result.metadata["authority"]["matched_rule_ids"] == [
        "allow_sandboxed_process"
    ]


@pytest.mark.asyncio
async def test_sandbox_execution_service_preserves_failed_on_success_sessions():
    runtime = LocalTestSandboxRuntime(
        commands={"fail": lambda request: SandboxExecResult(exit_code=2, stderr="no")}
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    result = await SandboxExecutionService(engine).execute(
        {
            "command": ["fail"],
            "manifest": {"lifecycle": {"cleanup": "on_success"}},
        }
    )

    assert result.ok is False
    assert list(runtime.sessions) == [result.metadata["sandbox_session_id"]]


@pytest.mark.asyncio
async def test_sandbox_execution_service_fails_closed_without_sandbox_route():
    engine = GovernanceEngine(_sandbox_policy(), sandbox_runtime=NoneSandboxRuntime())

    with pytest.raises(SandboxRequiredError):
        await SandboxExecutionService(engine).execute({"command": ["ok"]})


@pytest.mark.asyncio
async def test_sandbox_execution_service_rejects_mismatched_authority_request():
    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    with pytest.raises(ValueError, match="process.exec"):
        await SandboxExecutionService(engine).execute(
            SandboxCommandSpec(
                command=["ok"],
                authority_request=AuthorityRequest(
                    capability="workspace.files.read",
                    provider="workspace",
                    execution_surface="workspace",
                ),
            )
        )


def test_sandbox_execution_service_rejects_string_command():
    with pytest.raises(ValueError, match="list of argv"):
        SandboxCommandSpec(command="python script.py")
    with pytest.raises(ValueError, match="non-empty"):
        SandboxCommandSpec(command=["ok", ""])
    with pytest.raises(ValueError, match="strings"):
        SandboxCommandSpec(command=["ok", None])
    with pytest.raises(ValueError, match="strings"):
        SandboxCommandSpec(command=[123])
    with pytest.raises(ValueError, match="control"):
        SandboxCommandSpec(command=["ok\x00bad"])
    with pytest.raises(ValueError, match="control"):
        SandboxCommandSpec(command=["ok\nnext"])
    with pytest.raises(ValueError, match="absolute"):
        SandboxCommandSpec(command=["ok"], cwd="workspace")
    with pytest.raises(ValueError, match="escapes"):
        SandboxCommandSpec(command=["ok"], cwd="/workspace/%2e%2e/secret")
    with pytest.raises(ValueError, match="environment variable"):
        SandboxCommandSpec(command=["ok"], environment={"1BAD": "value"})
    with pytest.raises(ValueError, match="environment variable"):
        SandboxCommandSpec(command=["ok"], environment={"BAD=KEY": "value"})
    with pytest.raises(ValueError, match="environment variable"):
        SandboxCommandSpec(command=["ok"], environment={"NULL\x00KEY": "value"})
    with pytest.raises(ValueError, match="positive integer"):
        SandboxCommandSpec(command=["ok"], timeout_seconds=0)
    with pytest.raises(ValueError, match="positive integer"):
        SandboxCommandSpec(command=["ok"], timeout_seconds="30")


def test_sandbox_execution_service_preserves_argv_whitespace():
    spec = SandboxCommandSpec(command=["printf", " value "])

    assert spec.command == ["printf", " value "]


@pytest.mark.asyncio
async def test_sandbox_execution_service_filters_policy_metadata():
    class RecordingEngine(GovernanceEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.requests = []

        async def authorize_sandboxed(self, request):
            self.requests.append(request)
            return await super().authorize_sandboxed(request)

    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = RecordingEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(
            command=["ok", "--secret", "value"],
            metadata={"purpose": "test", "secret": "raw", "nested": {"x": "y"}},
        )
    )

    assert engine.requests[0].metadata == {
        "command": {"name": "ok", "argc": 3},
        "purpose": "test",
    }


@pytest.mark.asyncio
async def test_sandbox_execution_service_does_not_mutate_caller_authority_request():
    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )
    request = AuthorityRequest(
        capability="process.exec",
        target=None,
        metadata={"purpose": "test", "secret": "raw"},
    )

    await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(command=["ok"], authority_request=request)
    )

    assert request.provider is None
    assert request.execution_surface is None
    assert request.target is None
    assert request.metadata == {"purpose": "test", "secret": "raw"}


@pytest.mark.asyncio
async def test_sandbox_execution_service_forces_command_risk_high():
    class RecordingEngine(GovernanceEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.requests = []

        async def authorize_sandboxed(self, request):
            self.requests.append(request)
            return await super().authorize_sandboxed(request)

    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = RecordingEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(
            command=["ok"],
            authority_request=AuthorityRequest(
                capability="process.exec",
                risk_level="low",
            ),
        )
    )

    assert engine.requests[-1].risk_level == "high"


@pytest.mark.asyncio
async def test_sandbox_execution_service_authorizes_manifest_scope_before_create():
    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    with pytest.raises(UnknownCapabilityError, match="Unknown capability"):
        await SandboxExecutionService(engine).execute(
            {
                "command": ["ok"],
                "manifest": {
                    "network_policy": {
                        "default": "deny",
                        "allowed_hosts": ["api.example.com"],
                    }
                },
            }
        )

    assert runtime.sessions == {}


@pytest.mark.asyncio
async def test_sandbox_execution_service_runs_with_allowed_manifest_scope():
    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0, stdout="ok")}
    )
    engine = GovernanceEngine(
        _sandbox_policy(
            extra_allow=[
                PolicyRule(
                    rule_id="allow_sandbox_network",
                    effect=PolicyEffect.ALLOW,
                    capability="sandbox.network.configure",
                )
            ]
        ),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    result = await SandboxExecutionService(engine).execute(
        {
            "command": ["ok"],
            "manifest": {
                "network_policy": {
                    "default": "deny",
                    "allowed_hosts": ["api.example.com"],
                }
            },
        }
    )

    assert result.ok is True


@pytest.mark.asyncio
async def test_sandbox_execution_service_manifest_scope_is_policy_matchable():
    class RecordingEngine(GovernanceEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.batch_requests = []

        async def authorize_all(self, requests):
            self.batch_requests.append(requests)
            return await super().authorize_all(requests)

    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = RecordingEngine(
        _sandbox_policy(
            extra_allow=[
                PolicyRule(
                    rule_id="allow_api_network",
                    effect=PolicyEffect.ALLOW,
                    capability="sandbox.network.configure",
                    target={"host": "api.example.com"},
                ),
                PolicyRule(
                    rule_id="allow_workspace_cwd",
                    effect=PolicyEffect.ALLOW,
                    capability="sandbox.filesystem.cwd",
                    target={"path": "/workspace/app"},
                ),
                PolicyRule(
                    rule_id="allow_workspace_write",
                    effect=PolicyEffect.ALLOW,
                    capability="sandbox.filesystem.configure",
                    target={"path": "/workspace/out"},
                ),
            ]
        ),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    result = await SandboxExecutionService(engine).execute(
        {
            "command": ["ok"],
            "cwd": "/workspace/./app",
            "manifest": {
                "filesystem_policy": {"writable_paths": ["/workspace/out"]},
                "network_policy": {"allowed_hosts": ["api.example.com"]},
            },
        }
    )

    scope_requests = engine.batch_requests[0]
    assert result.ok is True
    assert [request.target.host for request in scope_requests if request.host] == [
        "api.example.com"
    ]
    assert [request.target.path for request in scope_requests if request.target.path] == [
        "/workspace/app",
        "/workspace/out",
    ]


@pytest.mark.asyncio
async def test_sandbox_execution_service_cleanup_never_preserves_session():
    runtime = LocalTestSandboxRuntime(
        commands={"ok": lambda request: SandboxExecResult(exit_code=0)}
    )
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    result = await SandboxExecutionService(engine).execute(
        {"command": ["ok"], "manifest": {"lifecycle": {"cleanup": "never"}}}
    )

    assert result.ok is True
    assert list(runtime.sessions) == [result.metadata["sandbox_session_id"]]


@pytest.mark.asyncio
async def test_sandbox_execution_service_cleanup_failure_preserves_execute_error():
    class CleanupFailureRuntime(LocalTestSandboxRuntime):
        async def terminate(self, session_id: str) -> None:
            raise RuntimeError("terminate exploded")

    def boom(request):
        raise RuntimeError("execute exploded")

    runtime = CleanupFailureRuntime(commands={"boom": boom})
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    with pytest.raises(RuntimeError, match="execute exploded") as exc:
        await SandboxExecutionService(engine).execute({"command": ["boom"]})

    assert any("terminate exploded" in note for note in exc.value.__notes__)


@pytest.mark.asyncio
async def test_sandbox_execution_service_success_cleanup_failure_runs_once():
    class CleanupFailureRuntime(LocalTestSandboxRuntime):
        def __init__(self):
            super().__init__(commands={"ok": lambda request: SandboxExecResult(exit_code=0)})
            self.terminate_calls = 0

        async def terminate(self, session_id: str) -> None:
            self.terminate_calls += 1
            raise RuntimeError("terminate exploded")

    runtime = CleanupFailureRuntime()
    engine = GovernanceEngine(
        _sandbox_policy(),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )

    with pytest.raises(RuntimeError, match="terminate exploded"):
        await SandboxExecutionService(engine).execute({"command": ["ok"]})

    assert runtime.terminate_calls == 1


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


def _sandbox_policy(extra_allow: list[PolicyRule] | None = None) -> PolicyEnvelope:
    return PolicyEnvelope(
        name="sandbox",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(
            allow=[
                PolicyRule(
                    rule_id="allow_sandboxed_process",
                    effect=PolicyEffect.ALLOW,
                    capability="process.exec",
                    constraints=PolicyConstraints(sandbox_required=True),
                )
            ]
            + list(extra_allow or [])
        ),
    )
