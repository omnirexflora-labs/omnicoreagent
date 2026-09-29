"""R13 (0.5.0rc1 gate): a crash's sandbox is removed with its run, not by a host-wide sweep.

After kill -9 the run's sandbox container kept running (sleep infinity) for
good: neither a restart nor the resumed run removed it, and the only documented
cleanup, cleanup_orphans(), removed every OmniCoreAgent container on the Docker
host, including other agents' live ones. Containers now carry their run's id;
resuming, retrying or abandoning a run removes that run's leftovers, and the
sweep can be scoped to one run.

Real Docker; skipped where it is unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core import runs as runs_module
from omnicoreagent.sandbox import SandboxManifest, build_sandbox_runtime
from test_execute_tool import needs_docker

pytestmark = [needs_docker, pytest.mark.asyncio]


async def _orphan(runtime, run_id: str):
    """What a killed process leaves: a live sandbox labelled with its run."""
    token = runs_module._CURRENT.set(SimpleNamespace(run_id=run_id))
    try:
        return await runtime.create(SandboxManifest())
    finally:
        runs_module._CURRENT.reset(token)


async def _alive(runtime, run_id: str) -> int:
    client = await runtime._docker()
    return len(client.containers.list(all=True, filters={"label": f"omnicoreagent.run={run_id}"}))


async def test_the_sweep_can_be_scoped_to_one_run():
    runtime = build_sandbox_runtime({"provider": "docker", "options": {"image": "alpine:3.20"}})
    await _orphan(runtime, "run_orphan_a")
    await _orphan(runtime, "run_orphan_b")
    assert await _alive(runtime, "run_orphan_a") == 1 and await _alive(runtime, "run_orphan_b") == 1
    try:
        assert await runtime.cleanup_orphans(run_id="run_orphan_a") == 1
        assert await _alive(runtime, "run_orphan_a") == 0
        assert await _alive(runtime, "run_orphan_b") == 1
    finally:
        await runtime.cleanup_orphans(run_id="run_orphan_b")


async def test_abandoning_a_run_removes_the_sandbox_its_dead_process_left():
    from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent
    import tempfile
    from pathlib import Path

    agent = await _agent(Path(tempfile.mkdtemp()), RecordingModel(WRITE_AND_DELETE, DELETE, "done"))
    paused = await agent.run("tidy up", session_id="orphan")
    assert paused["status"] == "awaiting_approval"
    runtime = build_sandbox_runtime({"provider": "docker", "options": {"image": "alpine:3.20"}})
    agent.agent.governance_engine.sandbox_runtime = runtime
    await _orphan(runtime, paused["run_id"])
    assert await _alive(runtime, paused["run_id"]) == 1  # the orphan is really there

    await agent.abandon_run(paused["run_id"], status="failed", reason="its worker died")

    assert await _alive(runtime, paused["run_id"]) == 0
