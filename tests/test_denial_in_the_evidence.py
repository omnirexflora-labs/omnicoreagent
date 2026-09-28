"""A denial is a decision in the evidence, like an approval.

Found by the Make It Safe stranger test (2026-09-28): after a person denied a
call, its governance entries held only the original `ask`; the denial and who
made it appeared only inside the text the model received. An approval was
recorded as an `allow` decision with `approved_by`; a denial now is a `deny`
decision with `denied_by`, and an expiry a `deny` by "system" with the reason
`expired_policy`.
"""

from __future__ import annotations

import pytest

from test_approval_expiry_and_fail_mode import _expire
from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent


async def _decisions_for(agent, run_id, call_id="d1"):
    story = await agent.get_run_trajectory(run_id)
    return [
        entry
        for segment in story["segments"]
        for step in (segment["trajectory"] or {}).get("steps", [])
        for call in step["tool_calls"]
        if call["tool_call_id"] == call_id
        for entry in call["governance"]
    ]


@pytest.mark.asyncio
async def test_a_persons_denial_is_recorded_as_their_decision(tmp_path):
    agent = await _agent(tmp_path, RecordingModel(WRITE_AND_DELETE, DELETE, "kept it"))
    paused = await agent.run("tidy up", session_id="deny-ev")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="deny", approver="carol", note="keep it"
    )
    await agent.resume(paused["run_id"])

    decisions = await _decisions_for(agent, paused["run_id"])
    denial = decisions[-1]
    assert denial["effect"] == "deny" and denial["reason_code"] == "denied"
    assert denial["denied_by"] == "carol" and denial["approved_by"] is None
    assert denial["approval_id"] == approval["approval_id"]
    (used,) = (await agent.get_run(paused["run_id"]))["approvals"]
    assert used["decision"] == "deny"


@pytest.mark.asyncio
async def test_an_expiry_is_recorded_as_a_denial_by_the_system(tmp_path):
    agent = await _agent(tmp_path, RecordingModel(WRITE_AND_DELETE, DELETE, "could not"))
    paused = await agent.run("tidy up", session_id="expire-ev")
    await _expire(agent, paused["run_id"])
    await agent.resume(paused["run_id"])

    denial = (await _decisions_for(agent, paused["run_id"]))[-1]
    assert denial["effect"] == "deny" and denial["reason_code"] == "expired_policy"
    assert denial["denied_by"] == "system"


@pytest.mark.asyncio
async def test_a_resumed_step_is_counted_once_in_the_runs_totals(tmp_path):
    # The stranger test read `steps: 4` for steps numbered 1, 2, 2, 3: the
    # step that paused and the same step resumed were counted apart.
    agent = await _agent(tmp_path, RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up"))
    paused = await agent.run("tidy up", session_id="steps-ev")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    await agent.resume(paused["run_id"])

    story = await agent.get_run_trajectory(paused["run_id"])
    numbers = {
        step["step"]
        for segment in story["segments"]
        for step in (segment["trajectory"] or {}).get("steps", [])
    }
    assert story["totals"]["steps"] == len(numbers)
