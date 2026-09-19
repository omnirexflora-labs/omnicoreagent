"""The sandbox backend interface: bring-your-own providers, sessions, routing."""

from __future__ import annotations

import pytest

from omnicoreagent.governance import (
    GovernanceEngine,
    PolicyConstraints,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    PolicyRuleSet,
)
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    NoneSandboxRuntime,
    SandboxCommandSpec,
    SandboxExecResult,
    SandboxExecutionService,
    SandboxManifest,
    SandboxRuntime,
    SandboxSession,
    build_sandbox_runtime,
    register_sandbox_provider,
    registered_sandbox_providers,
)


@pytest.fixture(autouse=True)
def _restore_provider_registry():
    """Providers registered by a test do not leak into other tests."""
    from omnicoreagent.sandbox import factory

    saved = dict(factory._PROVIDERS)
    yield
    factory._PROVIDERS.clear()
    factory._PROVIDERS.update(saved)


class AcmeSandbox(LocalTestSandboxRuntime):
    """A provider an application brings: registered by name, built from options."""

    provider = "acme"

    def __init__(self, *, options, telemetry_recorder=None):
        super().__init__(telemetry_recorder=telemetry_recorder)
        self.options = options

    async def create(self, manifest):
        session = await super().create(manifest)
        session.provider = "acme"
        manifest.provider = "acme"
        return session


def _policy() -> PolicyEnvelope:
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
        ),
    )


def test_an_application_registers_its_own_provider_by_name():
    register_sandbox_provider(
        "acme",
        lambda options, telemetry_recorder: AcmeSandbox(
            options=options, telemetry_recorder=telemetry_recorder
        ),
        replace=True,
    )

    runtime = build_sandbox_runtime({"provider": "acme", "options": {"image": "python:3.12"}})

    assert isinstance(runtime, AcmeSandbox)
    assert runtime.options == {"image": "python:3.12"}
    assert "acme" in registered_sandbox_providers()
    assert SandboxSession(session_id="s", provider="acme", manifest=SandboxManifest()).provider == "acme"


def test_an_unknown_provider_names_the_registered_ones():
    with pytest.raises(ValueError, match="Unknown sandbox provider 'nope'") as error:
        build_sandbox_runtime({"provider": "nope"})
    assert "local_test" in str(error.value) and "none" in str(error.value)


def test_a_provider_name_is_not_replaced_by_accident():
    register_sandbox_provider("acme", lambda options, telemetry_recorder: None, replace=True)
    with pytest.raises(ValueError, match="already registered"):
        register_sandbox_provider("acme", lambda options, telemetry_recorder: None)


def test_backends_state_whether_they_can_execute():
    assert NoneSandboxRuntime().supports_execution is False
    assert LocalTestSandboxRuntime().supports_execution is True
    assert SandboxRuntime.supports_execution is False


@pytest.mark.asyncio
async def test_files_move_in_and_out_of_a_session_in_batches():
    runtime = LocalTestSandboxRuntime()
    session = await runtime.create(SandboxManifest())

    await runtime.upload_files(session.session_id, {"/workspace/a.txt": b"one", "/workspace/b.txt": b"two"})
    files = await runtime.download_files(session.session_id, ["/workspace/a.txt", "/workspace/b.txt"])

    assert files == {"/workspace/a.txt": b"one", "/workspace/b.txt": b"two"}


@pytest.mark.asyncio
async def test_a_session_is_reused_across_governed_commands():
    authorized = []

    async def write(request, context):
        await runtime.write_file(context.session.session_id, "/workspace/state.txt", b"kept")
        return SandboxExecResult(exit_code=0, stdout="written")

    async def read(request, context):
        return SandboxExecResult(
            exit_code=0,
            stdout=(await runtime.read_file(context.session.session_id, "/workspace/state.txt")).decode(),
        )

    runtime = LocalTestSandboxRuntime(commands={"write": write, "read": read})
    engine = GovernanceEngine(_policy(), sandbox_runtime=runtime, allow_test_sandbox_runtime=True)
    original = engine.authorize_sandboxed

    async def counting(request):
        authorized.append(request.capability)
        return await original(request)

    engine.authorize_sandboxed = counting
    service = SandboxExecutionService(engine)

    session = await service.open_session(SandboxManifest())
    first = await service.execute(SandboxCommandSpec(command=["write"]), session=session)
    second = await service.execute(SandboxCommandSpec(command=["read"]), session=session)
    assert second.stdout == "kept"
    assert first.metadata["sandbox_session_id"] == second.metadata["sandbox_session_id"]
    # Every command is authorized, not only the first.
    assert authorized == ["process.exec", "process.exec"]

    await service.close_session(session)
    assert runtime.sessions == {}
    with pytest.raises(Exception, match="closed"):
        await service.execute(SandboxCommandSpec(command=["read"]), session=session)


@pytest.mark.asyncio
async def test_an_agent_exposes_a_governed_execution_route_only_with_an_executing_sandbox():
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    async def agent(governance_config=None):
        built = OmniCoreAgent(
            name="exec-agent",
            system_instruction="x",
            model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"},
            agent_config={
                "guardrail_mode": "off",
                "enable_workspace_files": False,
                **({"governance_config": governance_config} if governance_config else {}),
            },
        )
        # Governance (and its sandbox) is built when the agent initializes.
        await built.initialize()
        return built

    plain = await agent()
    assert plain.sandbox_execution is None
    assert plain.can_execute is False

    no_exec = await agent({"enabled": True, "policy": _policy(), "sandbox_config": "none"})
    assert no_exec.can_execute is False

    sandboxed = await agent(
        {
            "enabled": True,
            "policy": _policy(),
            "sandbox_config": "local_test",
            "allow_test_sandbox_runtime": True,
        }
    )
    assert isinstance(sandboxed.sandbox_execution, SandboxExecutionService)
    assert sandboxed.can_execute is True
