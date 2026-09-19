"""E7a: the Modal sandbox backend.

The unit tests drive a stand-in for the Modal SDK and check exactly what the
adapter asks Modal for. The live test runs against the machine's own Modal
account when one is configured, and is skipped with its reason otherwise.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from omnicoreagent.sandbox import (
    NetworkPolicy,
    SandboxAuthorityContext,
    SandboxExecRequest,
    SandboxManifest,
    build_sandbox_runtime,
)

AUTHORITY = SandboxAuthorityContext(
    authority_request_id="authreq_test", decision_id="decision_test",
    matched_rule_ids=["allow_sandboxed_execution"], reason_code="matched_allow",
)


class _Aio:
    """A Modal-style method: callable, with an .aio counterpart."""

    def __init__(self, function):
        self.function = function
        self.aio = function

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)


class FakeProcess:
    def __init__(self, sandbox, command, kwargs):
        sandbox.commands.append((command, kwargs))
        self.stdout = types.SimpleNamespace(read=_Aio(lambda: _done("out")))
        self.stderr = types.SimpleNamespace(read=_Aio(lambda: _done("err")))
        self.wait = _Aio(lambda: _done(0))


async def _done(value):
    return value


class FakeSandbox:
    def __init__(self, **kwargs):
        self.created_with = kwargs
        self.commands: list = []
        self.files: dict[str, bytes] = {}
        self.terminated = False
        self.object_id = "sb-123"
        self.exec = _Aio(lambda *command, **kw: _done(FakeProcess(self, command, kw)))
        self.terminate = _Aio(lambda **_: _done(self._terminate()))
        self.filesystem = FakeFilesystem(self)

    def _terminate(self):
        self.terminated = True


class FakeFilesystem:
    """Modal's sandbox filesystem API (write_bytes/read_bytes/make_directory)."""

    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.make_directory = _Aio(lambda path, create_parents=True: _done(None))
        self.write_bytes = _Aio(self._write)
        self.read_bytes = _Aio(self._read)

    async def _write(self, data, remote_path):
        self.sandbox.files[remote_path] = data

    async def _read(self, remote_path):
        return self.sandbox.files.get(remote_path, b"")


@pytest.fixture
def fake_modal(monkeypatch):
    created: dict = {}

    async def create(**kwargs):
        created["kwargs"] = kwargs
        created["sandbox"] = FakeSandbox(**kwargs)
        return created["sandbox"]

    module = types.ModuleType("modal")
    module.Sandbox = types.SimpleNamespace(create=_Aio(create))
    module.App = types.SimpleNamespace(lookup=_Aio(lambda name, **kw: _done(f"app:{name}")))
    module.Image = types.SimpleNamespace(from_registry=lambda image: f"image:{image}")
    monkeypatch.setitem(sys.modules, "modal", module)
    return created


@pytest.mark.asyncio
async def test_a_session_is_created_with_the_manifest_and_no_network(fake_modal):
    runtime = build_sandbox_runtime({"provider": "modal", "options": {"app_name": "tests"}})
    manifest = SandboxManifest(
        image="python:3.12-slim",
        environment={"plain": {"GREETING": "hello"}},
        resources={"cpu": "2", "memory": "512m"},
    )

    session = await runtime.create(manifest)
    kwargs = fake_modal["kwargs"]

    assert session.provider == "modal" and session.metadata["sandbox_id"] == "sb-123"
    assert kwargs["image"] == "image:python:3.12-slim"
    assert kwargs["app"] == "app:tests"
    assert kwargs["block_network"] is True
    assert kwargs["workdir"] == "/workspace"
    assert kwargs["env"] == {"GREETING": "hello"}
    assert kwargs["cpu"] == 2.0 and kwargs["memory"] == 512


@pytest.mark.asyncio
async def test_the_network_policy_becomes_modals_allowlist(fake_modal):
    runtime = build_sandbox_runtime({"provider": "modal"})

    await runtime.create(
        SandboxManifest(network_policy=NetworkPolicy(default="deny", allowed_hosts=["pypi.org"]))
    )

    kwargs = fake_modal["kwargs"]
    assert kwargs["block_network"] is False
    assert kwargs["outbound_domain_allowlist"] == ["pypi.org"]


@pytest.mark.asyncio
async def test_commands_and_files_go_through_modal(fake_modal):
    runtime = build_sandbox_runtime({"provider": "modal"})
    session = await runtime.create(SandboxManifest())

    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=["echo", "hi"], authority=AUTHORITY, timeout_seconds=7),
    )
    await runtime.write_file(session.session_id, "notes/a.txt", b"data")
    read_back = await runtime.read_file(session.session_id, "notes/a.txt")
    await runtime.terminate(session.session_id)

    sandbox = fake_modal["sandbox"]
    command, kwargs = sandbox.commands[0]
    assert command == ("echo", "hi") and kwargs["timeout"] == 7
    assert kwargs["workdir"] == "/workspace"
    assert (result.exit_code, result.stdout, result.stderr) == (0, "out", "err")
    assert read_back == b"data" and sandbox.files["/workspace/notes/a.txt"] == b"data"
    assert sandbox.terminated is True


@pytest.mark.asyncio
async def test_a_path_outside_the_working_directory_is_refused(fake_modal):
    runtime = build_sandbox_runtime({"provider": "modal"})
    session = await runtime.create(SandboxManifest())

    with pytest.raises(PermissionError):
        await runtime.read_file(session.session_id, "../../etc/passwd")


def test_the_modal_provider_is_registered_and_reports_execution():
    from omnicoreagent.sandbox import registered_sandbox_providers

    runtime = build_sandbox_runtime({"provider": "modal"})

    assert "modal" in registered_sandbox_providers()
    assert runtime.supports_execution is True and runtime.supports_required_sandbox is True


# --- live, against the machine's own Modal account ---------------------------


def _modal_configured() -> bool:
    try:
        import modal  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("MODAL_TOKEN_ID")) or (Path.home() / ".modal.toml").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not _modal_configured(), reason="Modal is not configured on this machine")
async def test_a_real_modal_sandbox_runs_a_command_and_moves_a_file():
    runtime = build_sandbox_runtime(
        {"provider": "modal", "options": {"app_name": "omnicoreagent-tests"}}
    )
    session = await runtime.create(SandboxManifest(image="python:3.12-slim"))
    try:
        result = await runtime.execute(
            session.session_id,
            SandboxExecRequest(command=["sh", "-c", "echo hello from modal"], authority=AUTHORITY),
        )
        await runtime.write_file(session.session_id, "note.txt", b"written by the test")
        back = await runtime.read_file(session.session_id, "note.txt")
        offline = await runtime.execute(
            session.session_id,
            SandboxExecRequest(
                command=["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 80), 5)"],
                authority=AUTHORITY,
            ),
        )
    finally:
        await runtime.terminate(session.session_id)

    assert result.exit_code == 0 and "hello from modal" in result.stdout
    assert back == b"written by the test"
    assert offline.exit_code != 0, "the sandbox has no network by default"
