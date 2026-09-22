"""Durable runs, D5: a paused run's sandbox is closed, and the model is told.

A run waiting for approval (or interrupted, or crashed) does not keep a
container. Workspace files are safe: the bridge copied them back after every
command. On resume the model is told the sandbox was reset, so it does not
count on files outside the workspace, installed packages, or processes.
Uses the real Docker backend (`alpine:3.20`).
"""

from __future__ import annotations

import json

import pytest

from test_execute_tool import _containers, needs_docker
from test_run_suspend import RecordingModel, _agent

pytestmark = [needs_docker, pytest.mark.asyncio]


async def test_a_paused_run_keeps_no_container_and_resumes_with_the_workspace_and_a_reset_notice(tmp_path):
    before = _containers()
    model = RecordingModel(
        [("e1", "execute", json.dumps({"command": "echo data > note.txt && echo tmp > /tmp/scratch"}))],
        [("w1", "write_file", json.dumps({"path": "old.txt", "content": "x"}))],
        [("d1", "delete_file", json.dumps({"path": "old.txt"}))],
        [("e2", "execute", json.dumps({"command": "cat note.txt; cat /tmp/scratch 2>&1 || true"}))],
        "done",
    )
    agent = await _agent(
        tmp_path,
        model,
        sandbox_config={"provider": "docker", "options": {"image": "alpine:3.20"}},
    )

    paused = await agent.run("tidy up", session_id="box")
    assert paused["status"] == "awaiting_approval"
    assert _containers() == before, "a waiting run keeps no container"
    assert (await agent.get_run(paused["run_id"]))["sandbox_used"] is True

    (approval,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="a")
    result = await agent.resume(paused["run_id"])

    assert result["response"] == "done"
    after_resume = json.dumps(model.calls[-2])
    assert "sandbox was reset" in after_resume and "workspace files are intact" in after_resume
    last_execute = next(m for m in model.calls[-1] if m.get("tool_call_id") == "e2")
    assert "data" in json.dumps(last_execute), "the workspace file came back"
    assert "No such file" in json.dumps(last_execute), "/tmp did not survive the reset"
    assert _containers() == before


async def test_a_run_that_never_used_a_sandbox_gets_no_notice(tmp_path):
    from test_run_suspend import DELETE, WRITE_AND_DELETE

    model = RecordingModel(WRITE_AND_DELETE, DELETE, "done")
    agent = await _agent(tmp_path, model, sandbox_config={"provider": "docker", "options": {"image": "alpine:3.20"}})
    paused = await agent.run("tidy up", session_id="nobox")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="a")
    await agent.resume(paused["run_id"])

    assert "sandbox was reset" not in json.dumps(model.calls)
