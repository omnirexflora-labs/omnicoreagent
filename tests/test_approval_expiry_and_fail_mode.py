"""An expired approval does not strand its run; fail mode says what happened.

Found writing the Approvals page (D6):
- An approval past its expiry could neither be decided ("expired at ...")
  nor let the run resume ("still waiting for approval"). An expired
  approval now ends that request: the call is refused as expired, the model
  is told, and the run finishes. A decision made in time still stands when
  the resume comes after the expiry.
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


def _refusal_for(model, call_id):
    """What the model was told about one call, on its last turn."""
    return json.dumps(
        next(m for m in _tool_messages(model.calls[-1]) if m["tool_call_id"] == call_id)
    ).lower()


@pytest.mark.asyncio
async def test_an_expired_approval_ends_the_request_and_the_run_finishes(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "could not delete it")
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

    finished = await agent.resume(paused["run_id"])

    assert finished["status"] == "success"
    assert finished["response"] == "could not delete it"
    assert _file(tmp_path, "old.txt").exists(), "nothing ran on an expired approval"
    told = _refusal_for(model, "d1")
    assert "expired" in told
    assert "waiting for a person" not in told
    record = await agent.get_run(paused["run_id"])
    assert [a["status"] for a in record["approvals"]] == ["expired"], "no new ask"


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
async def test_a_run_resumes_past_an_approval_nobody_decided_in_time(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "could not delete it")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="exp-2")
    await _expire(agent, paused["run_id"])

    finished = await agent.resume(paused["run_id"])

    assert finished["status"] == "success"
    assert _file(tmp_path, "old.txt").exists()
    assert "expired" in _refusal_for(model, "d1")
    record = await agent.get_run(paused["run_id"])
    assert [a["status"] for a in record["approvals"]] == ["expired"]
    # The trajectory's last word on the call: refused, not an error.
    story = await agent.get_run_trajectory(paused["run_id"])
    outcomes = [
        call["outcome"]
        for segment in story["segments"]
        for step in (segment["trajectory"] or {}).get("steps", [])
        for call in step["tool_calls"]
        if call["tool_call_id"] == "d1"
    ]
    assert outcomes[-1] == "denied"


@pytest.mark.asyncio
async def test_a_decision_made_in_time_stands_after_the_expiry(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "deleted")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="exp-3")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="approve", approver="alice"
    )
    await _expire(agent, paused["run_id"])  # the resume comes late

    finished = await agent.resume(paused["run_id"])

    assert finished["status"] == "success"
    assert not _file(tmp_path, "old.txt").exists(), "the approved delete ran"


@pytest.mark.asyncio
async def test_a_person_s_denial_reaches_the_model_as_theirs(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "archived instead")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="deny-1")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="deny",
        approver="bob", note="archive it instead",
    )

    await agent.resume(paused["run_id"])

    told = _refusal_for(model, "d1")
    assert "archive it instead" in told and "bob" in told
    assert "none can be asked" not in told
    assert "waiting for a person" not in told
