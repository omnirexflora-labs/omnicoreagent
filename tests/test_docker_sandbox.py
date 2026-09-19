"""The Docker sandbox backend against the real Docker daemon.

Skipped, with the reason, only where Docker or its Python SDK is unavailable.
Tests use the small `alpine:3.20` image.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

docker_sdk = pytest.importorskip("docker", reason="the Docker SDK is not installed")
if not hasattr(docker_sdk, "from_env"):
    pytest.skip("the Docker SDK is not installed", allow_module_level=True)
try:
    _client = docker_sdk.from_env()
    _client.ping()
except Exception as exc:  # pragma: no cover - depends on the machine
    pytest.skip(f"the Docker daemon is not reachable: {exc}", allow_module_level=True)

from omnicoreagent.governance import (  # noqa: E402
    GovernanceEngine,
    PolicyConstraints,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    PolicyRuleSet,
)
from omnicoreagent.sandbox import (  # noqa: E402
    NetworkPolicy,
    SandboxAuthorityContext,
    SandboxCommandSpec,
    SandboxExecRequest,
    SandboxExecutionService,
    SandboxManifest,
    SandboxUnsupportedError,
    build_sandbox_runtime,
)

IMAGE = "alpine:3.20"
LABEL = "omnicoreagent.sandbox"


def _runtime(**options):
    return build_sandbox_runtime({"provider": "docker", "options": {"image": IMAGE, **options}})


def _leftovers(session_id: str | None = None) -> list:
    label = f"omnicoreagent.session={session_id}" if session_id else LABEL
    return _client.containers.list(all=True, filters={"label": label})


# Direct backend calls carry the authority governance would attach.
AUTHORITY = SandboxAuthorityContext(
    authority_request_id="authreq_test",
    decision_id="decision_test",
    matched_rule_ids=["allow_sandboxed_process"],
    reason_code="matched_allow",
)


async def _run(runtime, session, *command, **request):
    return await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=list(command), authority=AUTHORITY, **request),
    )


@pytest_asyncio.fixture
async def session_of():
    opened = []

    async def open_(runtime, manifest=None):
        session = await runtime.create(manifest or SandboxManifest())
        opened.append((runtime, session))
        return session

    yield open_
    for runtime, session in opened:
        await runtime.terminate(session.session_id)


@pytest.mark.asyncio
async def test_commands_run_with_exit_code_and_separate_output(session_of):
    runtime = _runtime()
    session = await session_of(runtime)

    ok = await _run(runtime, session, "sh", "-c", "echo out; echo err >&2")
    failed = await _run(runtime, session, "sh", "-c", "exit 3")

    assert (ok.exit_code, ok.stdout.strip(), ok.stderr.strip()) == (0, "out", "err")
    assert failed.exit_code == 3 and failed.ok is False


@pytest.mark.asyncio
async def test_a_command_over_its_time_limit_is_stopped_and_the_session_stays_usable(session_of):
    runtime = _runtime()
    session = await session_of(runtime)

    slow = await _run(runtime, session, "sleep", "30", timeout_seconds=1)
    after = await _run(runtime, session, "echo", "still here")

    assert slow.timed_out is True and slow.ok is False
    assert after.stdout.strip() == "still here"


@pytest.mark.asyncio
async def test_network_is_off_by_default_and_an_allowlist_is_refused(session_of):
    runtime = _runtime()
    session = await session_of(runtime)

    interfaces = await _run(runtime, session, "ls", "/sys/class/net")
    assert interfaces.stdout.split() == ["lo"]

    manifest = SandboxManifest(network_policy=NetworkPolicy(default="deny", allowed_hosts=["pypi.org"]))
    with pytest.raises(SandboxUnsupportedError, match="allowlist"):
        await runtime.create(manifest)
    assert _leftovers() == [] or all(
        c.labels.get("omnicoreagent.session") != manifest.sandbox_id for c in _leftovers()
    )


@pytest.mark.asyncio
async def test_the_root_filesystem_is_read_only_and_the_working_directory_writable(session_of):
    runtime = _runtime()
    session = await session_of(runtime)

    root = await _run(runtime, session, "touch", "/etc/omnicoreagent")
    work = await _run(runtime, session, "sh", "-c", "echo hi > note.txt && cat note.txt")

    assert root.exit_code != 0
    assert work.stdout.strip() == "hi"


@pytest.mark.asyncio
async def test_files_move_in_and_out_and_stay_inside_the_working_directory(session_of):
    runtime = _runtime()
    session = await session_of(runtime)

    await runtime.upload_files(session.session_id, {"/workspace/data/in.txt": b"payload"})
    result = await _run(runtime, session, "sh", "-c", "tr a-z A-Z < data/in.txt > data/out.txt")
    files = await runtime.download_files(session.session_id, ["/workspace/data/out.txt"])

    assert result.ok and files == {"/workspace/data/out.txt": b"PAYLOAD"}
    for outside in ("/etc/passwd", "/workspace/../etc/passwd"):
        with pytest.raises(Exception, match="outside"):
            await runtime.read_file(session.session_id, outside)


@pytest.mark.asyncio
async def test_only_the_manifest_environment_reaches_the_container(session_of, monkeypatch):
    monkeypatch.setenv("HOST_ONLY_SECRET", "never-inside")
    runtime = _runtime()
    session = await session_of(runtime, SandboxManifest(environment={"plain": {"GREETING": "hello"}}))

    env = await _run(runtime, session, "env")

    assert "GREETING=hello" in env.stdout
    assert "never-inside" not in env.stdout


@pytest.mark.asyncio
async def test_memory_and_process_limits_are_applied(session_of):
    runtime = _runtime(pids_limit=64)
    session = await session_of(runtime, SandboxManifest(resources={"memory": "64m"}))

    memory = await _run(runtime, session, "cat", "/sys/fs/cgroup/memory.max")
    pids = await _run(runtime, session, "cat", "/sys/fs/cgroup/pids.max")

    assert memory.stdout.strip() == str(64 * 1024 * 1024)
    assert pids.stdout.strip() == "64"


@pytest.mark.asyncio
async def test_output_is_bounded(session_of):
    runtime = _runtime(max_output_bytes=1000)
    session = await session_of(runtime)

    big = await _run(runtime, session, "sh", "-c", "head -c 50000 /dev/zero | tr '\\0' x")

    assert len(big.stdout.encode()) <= 1000
    assert big.metadata["stdout_truncated"] is True


@pytest.mark.asyncio
async def test_terminate_leaves_no_container():
    runtime = _runtime()
    session = await runtime.create(SandboxManifest())
    assert len(_leftovers(session.session_id)) == 1

    await runtime.terminate(session.session_id)
    await runtime.terminate(session.session_id)  # idempotent

    assert _leftovers(session.session_id) == []


@pytest.mark.asyncio
async def test_governed_execution_runs_in_docker_and_cleans_up():
    policy = PolicyEnvelope(
        name="docker",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(
            allow=[
                PolicyRule(
                    rule_id="allow_sandboxed_process",
                    effect=PolicyEffect.ALLOW,
                    capability="process.exec",
                    constraints=PolicyConstraints(sandbox_required=True),
                ),
                PolicyRule(rule_id="allow_image", effect=PolicyEffect.ALLOW, capability="sandbox.image.use"),
            ]
        ),
    )
    runtime = _runtime()
    engine = GovernanceEngine(policy, sandbox_runtime=runtime)
    before = {c.id for c in _leftovers()}

    result = await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(command=["echo", "governed"], manifest=SandboxManifest(image=IMAGE))
    )

    assert result.stdout.strip() == "governed"
    assert result.metadata["sandbox_provider"] == "docker"
    assert result.metadata["authority"]["matched_rule_ids"] == ["allow_sandboxed_process"]
    assert {c.id for c in _leftovers()} == before


def test_the_docker_provider_is_registered_and_reports_execution():
    runtime = _runtime()

    assert runtime.provider == "docker"
    assert runtime.supports_execution is True
    assert runtime.supports_required_sandbox is True
    assert os.environ.get("DOCKER_HOST") is None or runtime is not None
