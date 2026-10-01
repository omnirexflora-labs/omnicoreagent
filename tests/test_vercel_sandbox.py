"""The Vercel sandbox adapter, against a stand-in for its SDK.

SP2 (simple-policy plan): a sandbox asked to have no network is checked from
inside, as E2B, Daytona and Modal are, and refused if it reaches one.
"""

from __future__ import annotations

import sys
import types

import pytest

from omnicoreagent.sandbox import NetworkPolicy, SandboxManifest, build_sandbox_runtime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError


class FakeVercelSandbox:
    def __init__(self, probe_exit):
        self.name = "vc-1"
        self.probe_exit = probe_exit
        self.runs: list = []
        self.destroyed = False
        self.fs = types.SimpleNamespace(mkdir=self._mkdir)

    async def _mkdir(self, path, recursive=True):
        return None

    async def run_process(self, program, args, **kwargs):
        self.runs.append([program, *args])
        probe = any("create_connection" in str(part) for part in args)
        return types.SimpleNamespace(
            returncode=self.probe_exit if probe else 0, stdout="", stderr=""
        )

    async def destroy(self):
        self.destroyed = True


@pytest.fixture
def fake_vercel(monkeypatch):
    state = {"probe_exit": 1}

    async def create_sandbox(**kwargs):
        state["kwargs"] = kwargs
        state["sandbox"] = FakeVercelSandbox(state["probe_exit"])
        return state["sandbox"]

    module = types.ModuleType("vercel.sandbox")
    module.create_sandbox = create_sandbox
    module.NetworkPolicy = lambda **kw: kw
    package = types.ModuleType("vercel")
    package.sandbox = module
    monkeypatch.setitem(sys.modules, "vercel", package)
    monkeypatch.setitem(sys.modules, "vercel.sandbox", module)
    return state


@pytest.mark.asyncio
async def test_a_sandbox_without_network_is_checked_from_inside(fake_vercel):
    session = await build_sandbox_runtime({"provider": "vercel"}).create(SandboxManifest())
    assert fake_vercel["kwargs"]["network_policy"]["mode"] == "deny-all"
    assert any("create_connection" in str(run) for run in fake_vercel["sandbox"].runs)
    assert session.metadata["network_isolation"] == "checked"


@pytest.mark.asyncio
async def test_a_sandbox_that_still_reaches_the_internet_is_refused(fake_vercel):
    fake_vercel["probe_exit"] = 0
    with pytest.raises(SandboxUnsupportedError, match="still reached the internet"):
        await build_sandbox_runtime({"provider": "vercel"}).create(SandboxManifest())
    assert fake_vercel["sandbox"].destroyed


@pytest.mark.asyncio
async def test_an_open_sandbox_is_not_checked(fake_vercel):
    session = await build_sandbox_runtime({"provider": "vercel"}).create(
        SandboxManifest(network_policy=NetworkPolicy(default="allow"))
    )
    assert session.metadata["network_isolation"] == "not required"
    assert not fake_vercel["sandbox"].runs
