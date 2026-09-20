"""Durable runs, D3: a run whose process died continues instead of starting over.

A live run refreshes a heartbeat on its record; once the heartbeat is older
than the lease, the run can be recovered (`agent.resume(run_id)`, or
`agent.run(..., run_id=...)` as background recovery does). Completed calls
never run again. A call that started but never finished runs again only if it
is idempotent; otherwise the model is told its outcome is unknown. A crash is
simulated in-process by raising `ProcessDied` (a BaseException no runtime
handler catches) inside a tool, so the record is left as a hard kill leaves
it; a real `SIGKILL` of a subprocess is the final proof. (`SystemExit` cannot
be used: asyncio lets it escape the event loop itself.)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_run_suspend import RecordingModel
from test_execute_tool import _MODEL


class ProcessDied(BaseException):
    """Stands in for the process dying: no graceful handler catches it."""


def _tools(ledger: Path, *, flaky_idempotent=False):
    tools = ToolRegistry()
    crashed = {"once": False}

    @tools.register_tool("charge", description="Charges the card (not idempotent).")
    def charge(amount: int) -> dict:
        with ledger.open("a") as f:
            f.write(f"charge {amount}\n")
        return {"status": "success", "data": {"charged": amount}}

    @tools.register_tool("report", description="Builds a report.", idempotent=flaky_idempotent)
    def report() -> dict:
        with ledger.open("a") as f:
            f.write("report\n")
        if not crashed["once"]:
            crashed["once"] = True
            raise ProcessDied()
        return {"status": "success", "data": {"report": "ready"}}

    return tools


async def _agent(model, tools, **config):
    agent = OmniCoreAgent(
        name="recoverable",
        system_instruction="Do the work.",
        model_config=_MODEL,
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "run_lease_seconds": 1, **config},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


async def _crash(agent, run_id="run_crash", session_id="crash"):
    with pytest.raises(ProcessDied):
        await agent.run("do it", session_id=session_id, run_id=run_id)
    return await agent.get_run(run_id)


TURNS = ([("c1", "charge", '{"amount": 5}')], [("r1", "report", "{}")])


@pytest.mark.asyncio
async def test_a_crashed_run_is_left_running_with_its_call_started(tmp_path):
    agent = await _agent(RecordingModel(*TURNS), _tools(tmp_path / "ledger"))

    run = await _crash(agent)

    assert run["status"] == "running"
    states = {c["tool_call_id"]: c["state"] for c in run["tool_calls"]}
    assert states == {"c1": "completed", "r1": "started"}
    assert run["owner"] and run["heartbeat_at"]


@pytest.mark.asyncio
async def test_a_live_run_cannot_be_taken_over(tmp_path):
    agent = await _agent(RecordingModel(*TURNS), _tools(tmp_path / "ledger"), run_lease_seconds=60)
    await _crash(agent)

    with pytest.raises(ValueError, match="running"):
        await agent.resume("run_crash")


@pytest.mark.asyncio
async def test_recovery_never_repeats_a_completed_call_and_reports_an_unknown_outcome(tmp_path):
    ledger = tmp_path / "ledger"
    model = RecordingModel(*TURNS, "recovered")
    agent = await _agent(model, _tools(ledger))
    await _crash(agent)
    await asyncio.sleep(1.2)  # the lease runs out

    result = await agent.resume("run_crash")

    assert result["status"] == "success" and result["response"] == "recovered"
    assert ledger.read_text().splitlines() == ["charge 5", "report"], "nothing ran twice"
    unknown = next(m for m in model.calls[-1] if m.get("tool_call_id") == "r1")
    assert "outcome is unknown" in json.dumps(unknown)
    run = await agent.get_run("run_crash")
    assert run["status"] == "completed"
    assert {c["tool_call_id"]: c["outcome"] for c in run["tool_calls"]}["r1"] == "unknown"


@pytest.mark.asyncio
async def test_an_interrupted_idempotent_call_runs_again(tmp_path):
    ledger = tmp_path / "ledger"
    model = RecordingModel(*TURNS, "recovered")
    agent = await _agent(model, _tools(ledger, flaky_idempotent=True))
    await _crash(agent)
    await asyncio.sleep(1.2)

    await agent.resume("run_crash")

    assert ledger.read_text().splitlines() == ["charge 5", "report", "report"]
    result = next(m for m in model.calls[-1] if m.get("tool_call_id") == "r1")
    assert "ready" in json.dumps(result)


@pytest.mark.asyncio
async def test_running_again_with_the_same_run_id_recovers_instead_of_starting_over(tmp_path):
    ledger = tmp_path / "ledger"
    model = RecordingModel(*TURNS, "recovered")
    agent = await _agent(model, _tools(ledger))
    crashed = await _crash(agent)
    await asyncio.sleep(1.2)
    # A dead run writes nothing: its last heartbeat is the one it had when it
    # died. (Seen failing once in a full suite with "heartbeat is current";
    # this names the writer if it happens again.)
    after = await agent.get_run("run_crash")
    assert after["heartbeat_at"] == crashed["heartbeat_at"], (after, crashed)

    # Background recovery calls run() again with the same run ID.
    result = await agent.run("do it", session_id="crash", run_id="run_crash")

    assert result["response"] == "recovered"
    assert ledger.read_text().splitlines() == ["charge 5", "report"]


@pytest.mark.asyncio
async def test_a_failed_run_retried_with_the_same_id_starts_a_new_attempt(tmp_path):
    class FailsOnce(RecordingModel):
        failed = False

        async def llm_call(self, messages, tools=None, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("provider down")
            return await super().llm_call(messages, tools, **kwargs)

    agent = await _agent(FailsOnce("second time lucky"), ToolRegistry())

    first = await agent.run("go", session_id="retry", run_id="run_retry")
    second = await agent.run("go", session_id="retry", run_id="run_retry")

    assert first["status"] == "error" and second["response"] == "second time lucky"
    run = await agent.get_run("run_retry")
    assert run["status"] == "completed" and run["attempt"] == 2
    assert [a["status"] for a in run["previous_attempts"]] == ["failed"]


def test_tools_declare_whether_they_are_idempotent():
    from types import SimpleNamespace

    from omnicoreagent.core.tools.native_catalog import NativeToolCatalog
    from omnicoreagent.core.workspace.tools import build_tool_registry_workspace_files

    registry = ToolRegistry()

    @registry.register_tool("lookup", description="Reads.", idempotent=True)
    def lookup() -> dict:
        return {}

    @registry.register_tool("send", description="Sends.")
    def send() -> dict:
        return {}

    build_tool_registry_workspace_files(registry=registry)

    def mcp_tool(name, **hints):
        return SimpleNamespace(
            name=name, description=name, inputSchema={"type": "object", "properties": {}},
            annotations=SimpleNamespace(**{"readOnlyHint": None, "idempotentHint": None, **hints}),
        )

    catalog = NativeToolCatalog(
        local_tools=registry,
        mcp_tools={"srv": [mcp_tool("get_doc", readOnlyHint=True), mcp_tool("put_doc", idempotentHint=True), mcp_tool("post_msg")]},
    )
    idempotent = {b.name: b.idempotent for b in catalog.bindings.values()}

    assert idempotent["lookup"] and not idempotent["send"]
    assert idempotent["read_file"] and idempotent["ls"] and not idempotent["write_file"]
    assert idempotent["get_doc"] and idempotent["put_doc"] and not idempotent["post_msg"]


# --- the proof: a real process killed mid-run --------------------------------

_WORKER = textwrap.dedent(
    """
    import asyncio, os, sys, time
    sys.path.insert(0, {tests!r})
    from omnicoreagent.core.memory_store.memory_router import MemoryRouter
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
    from test_run_suspend import RecordingModel
    from test_execute_tool import _MODEL

    ledger = {ledger!r}
    tools = ToolRegistry()

    @tools.register_tool("charge", description="Charges the card.")
    def charge(amount: int) -> dict:
        with open(ledger, "a") as f:
            f.write(f"charge {{amount}}\\n")
        return {{"status": "success", "data": {{"charged": amount}}}}

    @tools.register_tool("report", description="Builds a report.")
    async def report() -> dict:
        with open(ledger, "a") as f:
            f.write("report\\n")
        print("REPORT_STARTED", flush=True)
        await asyncio.sleep(60)  # killed here
        return {{"status": "success"}}

    async def main():
        agent = OmniCoreAgent(
            name="recoverable", system_instruction="Do the work.", model_config=_MODEL,
            local_tools=tools, memory_router=MemoryRouter("sql"),
            agent_config={{"guardrail_mode": "off", "enable_workspace_files": False, "run_lease_seconds": 1}},
        )
        await agent.initialize()
        agent.llm_connection = RecordingModel(
            [("c1", "charge", '{{"amount": 5}}')], [("r1", "report", "{{}}")]
        )
        await agent.run("do it", session_id="killed", run_id="run_killed")

    asyncio.run(main())
    """
)


@pytest.mark.asyncio
async def test_a_killed_process_is_finished_by_another_without_repeating_side_effects(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    from omnicoreagent.core.memory_store.memory_router import MemoryRouter

    ledger = tmp_path / "ledger"
    database = f"sqlite:///{tmp_path / 'runs.db'}"
    script = tmp_path / "worker.py"
    script.write_text(_WORKER.format(tests=str(Path(__file__).parent), ledger=str(ledger)))
    env = {**os.environ, "DATABASE_URL": database}
    worker = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, env=env)
    try:
        deadline = time.monotonic() + 60
        for line in worker.stdout:
            if "REPORT_STARTED" in line or time.monotonic() > deadline:
                break
        worker.send_signal(signal.SIGKILL)  # no cleanup of any kind
        worker.wait(timeout=10)
    finally:
        if worker.poll() is None:
            worker.kill()

    monkeypatch.setenv("DATABASE_URL", database)
    model = RecordingModel("finished after the crash")
    survivor = OmniCoreAgent(
        name="recoverable", system_instruction="Do the work.", model_config=_MODEL,
        local_tools=_tools(ledger), memory_router=MemoryRouter("sql"),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "run_lease_seconds": 1},
    )
    await survivor.initialize()
    survivor.llm_connection = model
    stranded = await survivor.get_run("run_killed")
    assert stranded["status"] == "running"
    await asyncio.sleep(1.2)

    result = await survivor.resume("run_killed")

    assert result["response"] == "finished after the crash"
    assert ledger.read_text().splitlines() == ["charge 5", "report"], "no side effect repeated"
    assert "outcome is unknown" in json.dumps(model.calls[-1])
    assert (await survivor.get_run("run_killed"))["status"] == "completed"
