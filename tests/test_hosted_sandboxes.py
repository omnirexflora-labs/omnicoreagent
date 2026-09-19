"""E7b: the E2B and Daytona sandbox backends.

Neither service has credentials on this machine, so the tests drive stand-ins
for their SDKs, built to the shape of the installed packages (`e2b` 2.51,
`daytona` 0.214), and check exactly what each adapter asks the service for.
Both adapters are also covered by the shared behaviour every backend must
have: manifest options honoured, network off by default, paths confined to
the working directory, and termination.
"""

from __future__ import annotations

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
    def __init__(self, calls):
        self.calls = calls

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return types.SimpleNamespace(exit_code=0, stdout="out", stderr="err", error=None)


class FakeE2BSandbox:
    def __init__(self, **kwargs):
        self.created_with = kwargs
        self.sandbox_id = "e2b-1"
        self.commands = FakeE2BCommands([])
        self.files = FakeE2BFiles({})
        self.killed = False

    async def kill(self):
        self.killed = True


@pytest.fixture
def fake_e2b(monkeypatch):
    made: dict = {}

    async def create(**kwargs):
        made["kwargs"] = kwargs
        made["sandbox"] = FakeE2BSandbox(**kwargs)
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
    command, options = fake_e2b["sandbox"].commands.calls[0]
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
    def __init__(self, params):
        self.params = params
        self.id = "dt-1"
        self.fs = FakeDaytonaFs()
        self.calls: list = []
        self.process = types.SimpleNamespace(exec=self._exec)

    async def _exec(self, command, cwd=None, env=None, timeout=None):
        self.calls.append((command, cwd, env, timeout))
        return types.SimpleNamespace(exit_code=0, result="combined output", artifacts=None)


@pytest.fixture
def fake_daytona(monkeypatch):
    made: dict = {}

    class FakeClient:
        def __init__(self, config=None):
            made["config"] = config

        async def create(self, params):
            made["params"] = params
            made["sandbox"] = FakeDaytonaSandbox(params)
            return made["sandbox"]

        async def delete(self, sandbox, timeout=60, wait=False):
            made["deleted"] = sandbox.id

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

    params = fake_daytona["params"]
    assert params["image"] == "python:3.12-slim" and params["network_block_all"] is True
    assert params["ephemeral"] is True and params["resources"] == {"cpu": 2, "memory": 2}
    assert fake_daytona["config"] == {"api_key": "k"}
    command, cwd, _, timeout = fake_daytona["sandbox"].calls[0]
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
