"""A resumed run runs the call the person approved, as the model made it.

Found by the repository steward's P3 rerun: its approved `push_files` asked
for approval a second time on resume, and the push that finally ran put
`email = "[REDACTED_EMAIL]"` into the repository's pyproject.toml — its
second corrupted pull request (#251, after #250). The paused call was
rebuilt from session memory, which the privacy filter redacts by default, so
its arguments were no longer the ones the model made, the approval's digest
no longer matched them, and a person was asked to approve the corrupted
call instead.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.governance import PolicyEffect, PolicyRule
from test_run_suspend import RecordingModel, _agent, _file, _policy

CONTENT = '[project]\nauthors = [{ name = "Ada", email = "ada@example.com" }]\n'


def _asking_policy():
    policy = _policy()
    policy.rules.ask.insert(
        0, PolicyRule(rule_id="ask_writes", effect=PolicyEffect.ASK, capability="workspace.files.write")
    )
    return policy


@pytest.mark.asyncio
async def test_an_approved_call_runs_with_the_arguments_it_was_approved_with(tmp_path, monkeypatch):
    import test_run_suspend

    monkeypatch.setattr(test_run_suspend, "_policy", _asking_policy)
    model = RecordingModel(
        [("w1", "write_file", json.dumps({"path": "pyproject.toml", "content": CONTENT}))],
        "written",
    )
    agent = await _agent(tmp_path, model)

    paused = await agent.run("write the project file", session_id="pii-resume")
    assert paused["status"] == "awaiting_approval", paused
    (approval,) = paused["approvals"]

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success", result
    record = await agent.get_run(paused["run_id"])
    assert [a["status"] for a in record["approvals"]] == ["used"], "one approval, used once"
    assert _file(tmp_path, "pyproject.toml").read_text() == CONTENT

