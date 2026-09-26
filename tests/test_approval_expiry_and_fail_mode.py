"""An expired approval does not strand its run; fail mode says what happened.

Found writing the Approvals page (D6):
- An approval past its expiry could neither be decided ("expired at ...")
  nor let the run resume ("still waiting for approval"): the expiry was
  only recorded during a resume, which it blocked. Deciding it now records
  it as expired, and a resume asks again with a fresh approval.
- With approval_mode="fail", a call that needed a person was refused, but
  the model was told it was "waiting for a person's approval" and the
  trajectory said awaiting_approval, though nobody would ever be asked.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent, _file, _tool_messages


async def _expire(agent, run_id):
    record = await agent.memory_router.get_run_state(run_id)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    for approval in record["approvals"]:
        approval["expires_at"] = past
    version = record.pop("version")
    await agent.memory_router.save_run_state(record, expected_version=version)


@pytest.mark.asyncio
async def test_an_expired_approval_is_recorded_and_the_run_asks_again(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="exp-1")
    (approval,) = paused["approvals"]
    await _expire(agent, paused["run_id"])

    with pytest.raises(ValueError, match="expired"):
        await agent.resolve_approval(
            paused["run_id"], approval["approval_id"], decision="approve", approver="alice"
        )
    record = await agent.get_run(paused["run_id"])
    assert [a["status"] for a in record["approvals"]] == ["expired"]

    again = await agent.resume(paused["run_id"])

    assert again["status"] == "awaiting_approval"
    (fresh,) = again["approvals"]
    assert fresh["approval_id"] != approval["approval_id"]
    assert _file(tmp_path, "old.txt").exists(), "nothing ran on an expired approval"


@pytest.mark.asyncio
async def test_fail_mode_tells_the_model_the_call_was_refused(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "could not delete")
    agent = await _agent(tmp_path, model, approval_mode="fail")

    result = await agent.run("tidy up", session_id="fail-1")

    refusal = next(m for m in _tool_messages(model.calls[-1]) if m["tool_call_id"] == "d1")
    assert "waiting for a person" not in json.dumps(refusal).lower()
    story = await agent.get_run_trajectory(result["run_id"])
    calls = [c for step in story["segments"][0]["trajectory"]["steps"] for c in step["tool_calls"]]
    delete = next(c for c in calls if c["tool_call_id"] == "d1")
    assert delete["outcome"] == "denied"


@pytest.mark.asyncio
async def test_a_run_can_resume_past_an_approval_nobody_decided_in_time(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="exp-2")
    await _expire(agent, paused["run_id"])

    again = await agent.resume(paused["run_id"])

    assert again["status"] == "awaiting_approval"
    record = await agent.get_run(paused["run_id"])
    assert [a["status"] for a in record["approvals"]][0] == "expired"
