"""A listed run's approvals carry the arguments of the call, as get_run's do.

Found writing Stores and scale (D7): a second process finding what waits
with `list_runs(status="awaiting_approval")` got approvals without their
`arguments` (a KeyError), while `get_run` had them: an approver deciding from
the list could not see what they were approving.
"""

from __future__ import annotations

import pytest

from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent


@pytest.mark.asyncio
async def test_listed_approvals_carry_their_arguments(tmp_path):
    agent = await _agent(tmp_path, RecordingModel(WRITE_AND_DELETE, DELETE, "done"))
    paused = await agent.run("tidy up", session_id="list-1")

    (listed,) = await agent.list_runs(status="awaiting_approval")
    one = await agent.get_run(paused["run_id"])

    assert listed["approvals"][0]["arguments"] == one["approvals"][0]["arguments"] == {"path": "old.txt"}
