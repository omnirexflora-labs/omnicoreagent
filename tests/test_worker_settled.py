"""0.5.1 B2: a worker whose lead crashed does not stay `running`.

The 0.5.0 known issue: a worker spawned by `spawn_subagents`, left behind by a
lead process that died, kept status `running` until someone resumed or
abandoned it by hand. When the lead's run ends (finished after a resume,
abandoned or cancelled), the workers it started that are still `running` with
a lapsed lease are marked `abandoned`, with a reason naming the lead's run. A
worker whose own lease is alive is never touched.

The crash is made by hand: a lead whose process dies on its model call, and a
worker run that its delegation names, left `running` with the heartbeat a dead
process would have left. (A `ProcessDied` raised inside a worker is caught by
the spawn path and reported to the lead as an error, so it never leaves the
lead dead.)
"""

from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.core.runs import RunTracker, update_from_outside
from test_delegated_approvals import _lead
from test_run_recovery import ProcessDied
from test_run_suspend import RecordingModel


class DyingModel(RecordingModel):
    """A model whose process dies on its first call."""

    async def llm_call(self, messages, tools=None, **kwargs):
        raise ProcessDied()


async def _lead_crashed_mid_worker(tmp_path, *, worker_lease: int):
    lead = await _lead(tmp_path, DyingModel())
    lead.agent_config["run_lease_seconds"] = 1
    with pytest.raises(ProcessDied):
        await lead.run("tidy up", session_id="team", run_id="run_lead")

    # The worker the lead had started: running, owned by a process, with a
    # heartbeat and the lease that process kept.
    tracker = RunTracker(
        lead.memory_router,
        run_id="run_worker",
        session_id="team",
        agent_name="subagent_cleaner",
        lease_seconds=worker_lease,
    )
    await tracker.start("trace_worker")
    await update_from_outside(
        lead.memory_router,
        "run_lead",
        lambda r: r.__setitem__(
            "delegations", [{"tool_call_id": "s1", "name": "cleaner", "child_run_id": "run_worker"}]
        ),
    )
    assert (await lead.get_run("run_lead"))["status"] == "running"
    assert (await lead.get_run("run_worker"))["status"] == "running"
    lead.llm_connection = RecordingModel("all tidy")
    return lead


@pytest.mark.asyncio
async def test_a_worker_left_by_a_crashed_lead_is_settled_when_the_lead_resumes_and_finishes(tmp_path):
    lead = await _lead_crashed_mid_worker(tmp_path, worker_lease=1)
    await asyncio.sleep(1.2)  # both leases run out

    result = await lead.resume("run_lead")

    assert result["status"] == "success", result
    settled = await lead.get_run("run_worker")
    assert settled["status"] == "abandoned", settled
    assert "run_lead" in settled["error"]["message"]
    assert (await lead.get_run("run_lead"))["status"] == "completed"


@pytest.mark.asyncio
async def test_a_worker_whose_lease_is_alive_is_not_touched(tmp_path):
    lead = await _lead_crashed_mid_worker(tmp_path, worker_lease=600)
    await asyncio.sleep(1.2)  # the lead's lease runs out, the worker's does not

    result = await lead.resume("run_lead")

    assert result["status"] == "success", result
    assert (await lead.get_run("run_worker"))["status"] == "running"


@pytest.mark.asyncio
async def test_abandoning_the_lead_settles_its_lapsed_workers(tmp_path):
    lead = await _lead_crashed_mid_worker(tmp_path, worker_lease=1)
    await asyncio.sleep(1.2)

    await lead.abandon_run("run_lead", status="cancelled", reason="cancelled by the operator")

    settled = await lead.get_run("run_worker")
    assert settled["status"] == "abandoned" and "run_lead" in settled["error"]["message"]
    assert (await lead.get_run("run_lead"))["status"] == "cancelled"
