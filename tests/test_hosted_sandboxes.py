"""E7b: the E2B and Daytona sandbox backends.

The unit tests drive stand-ins for their SDKs, built to the shape of the
installed packages (`e2b` 2.51, `daytona` 0.214), and check exactly what each
adapter asks the service for. The live tests run against the account whose key
is in the environment, and are skipped with their reason otherwise. Both
adapters are also covered by the shared behaviour every backend must have:
manifest options honoured, network off by default, paths confined to the
working directory, and termination.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from omnicoreagent.sandbox import (
    NetworkPolicy,
    SandboxAuthorityContext,
    SandboxExecRequest,
    SandboxManifest,
    SandboxUnsupportedError,
    build_sandbox_runtime,
)

# Part of the command each adapter runs to check its own isolation.
REACHING_OUT_MARKER = "create_connection"

AUTHORITY = SandboxAuthorityContext(
    authority_request_id="authreq_test", decision_id="decision_test",
    matched_rule_ids=["allow_sandboxed_execution"], reason_code="matched_allow",
)


# --- E2B ---------------------------------------------------------------------


class FakeE2BFiles:
    def __init__(self, store):
        self.store = store
        self.directories: list[str] = []

    async def make_dir(self, path, **_):
        self.directories.append(path)
        return True

    async def write(self, path, data, **_):
        self.store[path] = data

    async def read(self, path, format="text", **_):
        return self.store.get(path, b"")


class FakeE2BCommands:
    """Commands succeed; the isolation probe fails, as a blocked sandbox does."""

    def __init__(self, calls, probe_exit_code=1):
        self.calls = calls
        self.probe_exit_code = probe_exit_code

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if REACHING_OUT_MARKER in command:
            return types.SimpleNamespace(
                exit_code=self.probe_exit_code, stdout="", stderr="", error=None
            )
        return types.SimpleNamespace(exit_code=0, stdout="out", stderr="err", error=None)


class FakeE2BSandbox:
    def __init__(self, probe_exit_code=1, **kwargs):
        self.created_with = kwargs
        self.sandbox_id = "e2b-1"
        self.commands = FakeE2BCommands([], probe_exit_code)
        self.files = FakeE2BFiles({})
        self.killed = False

    async def kill(self):
        self.killed = True


@pytest.fixture
def fake_e2b(monkeypatch):
    made: dict = {"probe_exit_code": 1}

    async def create(**kwargs):
        made["kwargs"] = kwargs
        made["sandbox"] = FakeE2BSandbox(made["probe_exit_code"], **kwargs)
        return made["sandbox"]

    module = types.ModuleType("e2b")
    module.AsyncSandbox = types.SimpleNamespace(create=create)
    monkeypatch.setitem(sys.modules, "e2b", module)
    return made


@pytest.mark.asyncio
async def test_e2b_creates_a_sandbox_without_internet_and_runs_commands(fake_e2b):
    runtime = build_sandbox_runtime({"provider": "e2b", "options": {"template": "base"}})
    session = await runtime.create(
        SandboxManifest(environment={"plain": {"GREETING": "hello"}})
    )

    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=["echo", "a b"], authority=AUTHORITY, timeout_seconds=5),
    )
    await runtime.write_file(session.session_id, "note.txt", b"data")
    back = await runtime.read_file(session.session_id, "note.txt")
    await runtime.terminate(session.session_id)

    kwargs = fake_e2b["kwargs"]
    assert kwargs["allow_internet_access"] is False
    assert kwargs["envs"] == {"GREETING": "hello"} and kwargs["template"] == "base"
    # The first command is the adapter's own check that the sandbox has no network.
    command, options = [
        call for call in fake_e2b["sandbox"].commands.calls
        if REACHING_OUT_MARKER not in call[0]
    ][0]
    # argv is quoted into one shell command, so the arguments survive intact
    assert command == "echo 'a b'" and options["cwd"] == "/workspace" and options["timeout"] == 5
    assert (result.exit_code, result.stdout, result.stderr) == (0, "out", "err")
    assert back == b"data" and "/workspace/note.txt" in fake_e2b["sandbox"].files.store
    assert fake_e2b["sandbox"].killed is True


@pytest.mark.asyncio
async def test_e2b_refuses_a_host_allowlist_and_paths_outside_the_working_directory(fake_e2b):
    runtime = build_sandbox_runtime({"provider": "e2b"})

    with pytest.raises(SandboxUnsupportedError, match="allowlist"):
        await runtime.create(
            SandboxManifest(network_policy=NetworkPolicy(default="deny", allowed_hosts=["pypi.org"]))
        )
    session = await runtime.create(SandboxManifest(network_policy=NetworkPolicy(default="allow")))
    assert fake_e2b["kwargs"]["allow_internet_access"] is True
    with pytest.raises(PermissionError):
        await runtime.write_file(session.session_id, "../escape.txt", b"x")


# --- Daytona -----------------------------------------------------------------


class FakeDaytonaFs:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.folders: list[str] = []

    async def create_folder(self, path, mode="755"):
        self.folders.append(path)

    async def upload_file(self, data, path, timeout=1800):
        self.files[path] = data

    async def download_file(self, path):
        return self.files.get(path, b"")


class FakeDaytonaSandbox:
    def __init__(self, params, probe_exit_code=1):
        self.params = params
        self.id = "dt-1"
        self.fs = FakeDaytonaFs()
        self.calls: list = []
        self.probe_exit_code = probe_exit_code
        self.process = types.SimpleNamespace(exec=self._exec)

    async def _exec(self, command, cwd=None, env=None, timeout=None):
        self.calls.append((command, cwd, env, timeout))
        if REACHING_OUT_MARKER in command:
            return types.SimpleNamespace(exit_code=self.probe_exit_code, result="", artifacts=None)
        return types.SimpleNamespace(exit_code=0, result="combined output", artifacts=None)


@pytest.fixture
def fake_daytona(monkeypatch):
    made: dict = {"probe_exit_code": 1}

    class FakeClient:
        def __init__(self, config=None):
            made["config"] = config

        async def create(self, params):
            made["params"] = params
            made["sandbox"] = FakeDaytonaSandbox(params, made["probe_exit_code"])
            return made["sandbox"]

        async def delete(self, sandbox, timeout=60, wait=False):
            made["deleted"] = sandbox.id

        async def close(self):
            made["closed"] = True

    module = types.ModuleType("daytona")
    module.AsyncDaytona = FakeClient
    module.DaytonaConfig = lambda **kwargs: kwargs
    module.CreateSandboxFromImageParams = lambda **kwargs: kwargs
    module.Resources = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "daytona", module)
    return made


@pytest.mark.asyncio
async def test_daytona_blocks_the_network_and_runs_commands(fake_daytona):
    runtime = build_sandbox_runtime({"provider": "daytona", "options": {"api_key": "k"}})
    session = await runtime.create(
        SandboxManifest(image="python:3.12-slim", resources={"cpu": "2", "memory": "2g"})
    )

    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=["echo", "a b"], authority=AUTHORITY, timeout_seconds=9),
    )
    await runtime.write_file(session.session_id, "out/note.txt", b"data")
    back = await runtime.read_file(session.session_id, "out/note.txt")
    await runtime.terminate(session.session_id)

    # The last session closes the client, rather than leaving its HTTP
    # connections open for the life of the process.
    assert fake_daytona.get("closed") is True

    params = fake_daytona["params"]
    assert params["image"] == "python:3.12-slim" and params["network_block_all"] is True
    assert params["ephemeral"] is True and params["resources"] == {"cpu": 2, "memory": 2}
    assert fake_daytona["config"] == {"api_key": "k"}
    # The first command is the adapter's own check that the sandbox has no network.
    command, cwd, _, timeout = [
        call for call in fake_daytona["sandbox"].calls if REACHING_OUT_MARKER not in call[0]
    ][0]
    assert command == "echo 'a b'" and cwd == "/workspace" and timeout == 9
    # Daytona returns one combined stream
    assert result.stdout == "combined output" and result.stderr == ""
    assert result.metadata["combined_output"] is True
    assert back == b"data" and fake_daytona["deleted"] == "dt-1"


@pytest.mark.asyncio
async def test_daytona_honours_a_host_allow_list(fake_daytona):
    runtime = build_sandbox_runtime({"provider": "daytona"})

    await runtime.create(
        SandboxManifest(network_policy=NetworkPolicy(default="deny", allowed_hosts=["pypi.org"]))
    )

    params = fake_daytona["params"]
    assert params["network_block_all"] is False
    assert params["network_allow_list"] == ["pypi.org"]


@pytest.mark.parametrize("provider", ["e2b", "daytona"])
def test_the_hosted_providers_are_registered_and_report_execution(provider):
    from omnicoreagent.sandbox import registered_sandbox_providers

    runtime = build_sandbox_runtime({"provider": provider})

    assert provider in registered_sandbox_providers()
    assert runtime.supports_execution is True and runtime.supports_required_sandbox is True


@pytest.mark.parametrize("provider", ["e2b", "daytona", "modal"])
def test_an_unknown_option_is_refused(provider):
    with pytest.raises(ValueError, match="Unknown"):
        build_sandbox_runtime({"provider": provider, "options": {"nonsense": 1}})


# --- live, against the account whose key is in the environment ---------------

LIVE_IMAGE = "python:3.12-slim"
REACHING_OUT = ["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 80), 5)"]


def _installed(module: str) -> bool:
    from importlib.util import find_spec

    return find_spec(module) is not None


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.environ.get("E2B_API_KEY") and _installed("e2b")),
    reason="E2B_API_KEY is not set, or the e2b extra is not installed",
)
async def test_a_real_e2b_sandbox_runs_a_command_and_moves_a_file():
    runtime = build_sandbox_runtime({"provider": "e2b"})
    session = await runtime.create(
        SandboxManifest(working_dir="/home/user/work", network_policy={"default": "allow"})
    )
    try:
        ran = await runtime.execute(
            session.session_id,
            SandboxExecRequest(command=["sh", "-c", "echo hello from e2b"], authority=AUTHORITY),
        )
        await runtime.write_file(session.session_id, "note.txt", b"written by the test")
        back = await runtime.read_file(session.session_id, "note.txt")
        outside = None
        try:
            await runtime.read_file(session.session_id, "../escaped.txt")
        except PermissionError as refused:
            outside = refused
    finally:
        await runtime.terminate(session.session_id)

    assert ran.exit_code == 0 and "hello from e2b" in ran.stdout
    assert back == b"written by the test"
    assert outside is not None, "a path outside the working directory is refused"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.environ.get("DAYTONA_API_KEY") and _installed("daytona")),
    reason="DAYTONA_API_KEY is not set, or the daytona extra is not installed",
)
async def test_a_real_daytona_sandbox_runs_a_command_and_moves_a_file():
    runtime = build_sandbox_runtime({"provider": "daytona"})
    session = await runtime.create(
        SandboxManifest(
            image=LIVE_IMAGE,
            working_dir="/home/daytona/work",
            network_policy={"default": "allow"},
        )
    )
    try:
        ran = await runtime.execute(
            session.session_id,
            SandboxExecRequest(command=["sh", "-c", "echo hello from daytona"], authority=AUTHORITY),
        )
        await runtime.write_file(session.session_id, "note.txt", b"written by the test")
        back = await runtime.read_file(session.session_id, "note.txt")
    finally:
        await runtime.terminate(session.session_id)

    assert ran.exit_code == 0 and "hello from daytona" in ran.stdout
    assert back == b"written by the test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, key, package, working_dir, image",
    [
        # E2B names a template of its own; Daytona takes a container image.
        ("e2b", "E2B_API_KEY", "e2b", "/home/user/work", None),
        ("daytona", "DAYTONA_API_KEY", "daytona", "/home/daytona/work", LIVE_IMAGE),
    ],
)
async def test_a_real_sandbox_is_never_handed_back_with_the_network_still_open(
    provider, key, package, working_dir, image
):
    """Either the service isolates the sandbox, or the adapter refuses it.

    Against the accounts this was written on, both services accept "no
    internet", record it, and hand back a sandbox that still reaches the
    internet — so this test usually takes the refusal branch. It passes either
    way; what it forbids is a sandbox that quietly has a network.
    """
    if not (os.environ.get(key) and _installed(package)):
        pytest.skip(f"{key} is not set, or the {package} extra is not installed")

    runtime = build_sandbox_runtime({"provider": provider})
    manifest = SandboxManifest(image=image, working_dir=working_dir)
    session = None
    try:
        session = await runtime.create(manifest)
    except SandboxUnsupportedError as refused:
        assert "network" in str(refused)
        return
    try:
        offline = await runtime.execute(
            session.session_id,
            SandboxExecRequest(command=REACHING_OUT, authority=AUTHORITY),
        )
    finally:
        await runtime.terminate(session.session_id)

    assert session.metadata["network_isolation"] == "checked"
    assert offline.exit_code != 0, "the sandbox reaches the internet with nothing allowing it"
