"""What a run turned out to be worth, attached whenever that is known.

Traces for training plan, R1. A run's own result is not its outcome: a
steward's pull request is merged an hour later, a customer accepts an answer
the next day, a migration's tests are run by CI afterwards. rLLM's
single-rollout training centres each run's reward against the batch mean,
which needs exactly this: one number per run, recorded when it arrives.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniServe, OmniServeConfig
from test_run_suspend import RecordingModel, _agent


async def _finished(tmp_path):
    agent = await _agent(tmp_path, RecordingModel("all done"))
    result = await agent.run("tidy up", session_id="outcomes")
    assert result["status"] == "success"
    return agent, result


@pytest.mark.asyncio
async def test_an_outcome_reaches_the_run_and_its_trace(tmp_path):
    agent, result = await _finished(tmp_path)

    recorded = await agent.record_outcome(
        result["run_id"], reward=1.0, label="merged", source="github", detail={"pull_request": 252}
    )

    assert recorded["reward"] == 1.0 and recorded["outcome_id"]
    record = await agent.get_run(result["run_id"])
    (outcome,) = record["outcomes"]
    assert (outcome["label"], outcome["source"], outcome["detail"]) == ("merged", "github", {"pull_request": 252})
    assert outcome["recorded_at"]
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    (event,) = [e for e in trace.events if e.event_type == "run_outcome"]
    assert event.output["reward"] == 1.0 and event.output["source"] == "github"


@pytest.mark.asyncio
async def test_a_run_can_gather_more_than_one_outcome(tmp_path):
    agent, result = await _finished(tmp_path)

    await agent.record_outcome(result["run_id"], reward=0.5, label="approved", source="reviewer")
    await agent.record_outcome(result["run_id"], reward=1.0, label="merged", source="github")

    record = await agent.get_run(result["run_id"])
    assert [o["label"] for o in record["outcomes"]] == ["approved", "merged"]
    assert [o["reward"] for o in record["outcomes"]] == [0.5, 1.0]


@pytest.mark.asyncio
async def test_an_outcome_needs_a_run_and_a_source(tmp_path):
    agent, result = await _finished(tmp_path)

    with pytest.raises(LookupError):
        await agent.record_outcome("run_unknown", reward=1.0, source="github")
    with pytest.raises(ValueError, match="source"):
        await agent.record_outcome(result["run_id"], reward=1.0, source=" ")


@pytest.mark.asyncio
async def test_the_trajectory_carries_the_outcomes(tmp_path):
    agent, result = await _finished(tmp_path)
    await agent.record_outcome(result["run_id"], reward=1.0, label="merged", source="github")

    trajectory = await agent.get_trajectory(result["trace_id"])

    assert [o["label"] for o in trajectory["outcomes"]] == ["merged"]


def test_an_outcome_can_be_recorded_over_http(tmp_path):
    agent = asyncio.run(_agent(tmp_path, RecordingModel("all done")))
    serve = OmniServe(agent, OmniServeConfig(request_timeout=10))
    with TestClient(serve.app) as client:
        run = client.post("/run/sync", json={"query": "tidy up", "session_id": "served"}).json()

        response = client.post(
            f"/runs/{run['run_id']}/outcome",
            json={"reward": 1.0, "label": "merged", "source": "github", "detail": {"pr": 252}},
        )

        assert response.status_code == 200, response.text
        assert response.json()["reward"] == 1.0
        view = client.get(f"/runs/{run['run_id']}").json()
        assert [o["label"] for o in view["outcomes"]] == ["merged"]
        assert client.post("/runs/run_missing/outcome", json={"reward": 1.0, "source": "x"}).status_code == 404
        assert "merged" in json.dumps(view)
