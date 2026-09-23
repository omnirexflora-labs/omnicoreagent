"""One record per run, for a trainer or an evaluator.

Traces for training plan, R4. rLLM's real-time RL consumes a batch of
finished runs: each one's messages as the model saw them, what it produced,
what the tools answered, the policy that served it, and the reward that
arrived later. This is that batch, read out of the traces.
"""

from __future__ import annotations

import json

import pytest

from test_telemetry_tool_record import ScriptedModel, _agent


@pytest.mark.asyncio
async def test_a_finished_run_becomes_one_training_record():
    agent = await _agent(ScriptedModel(("call_1", "lookup", '{"key": "a"}')))
    result = await agent.run("go", session_id="training")
    await agent.record_outcome(result["run_id"], reward=1.0, label="accepted", source="reviewer")

    (record,) = await agent.training_records(run_id=result["run_id"])

    assert record["run_id"] == result["run_id"] and record["trace_id"] == result["trace_id"]
    assert record["status"] == "completed" and record["session_id"] == "training"
    assert [o["reward"] for o in record["outcomes"]] == [1.0]
    assert record["totals"]["tokens"] is not None and record["evidence_status"]
    first, second = record["steps"]
    assert first["messages"][0]["role"] == "system" and first["tools"]
    assert [c["name"] for c in first["tool_calls"]] == ["lookup"]
    assert json.loads(first["tool_calls"][0]["arguments"]) == {"key": "a"}
    assert first["tool_calls"][0]["outcome"] == "success" and first["tool_calls"][0]["observation"]
    assert second["response"]["content"] == "done"
    assert "policy_version" in record


@pytest.mark.asyncio
async def test_records_can_be_read_for_a_session_and_written_as_jsonl(tmp_path):
    agent = await _agent(ScriptedModel(("call_1", "lookup", '{"key": "a"}')))
    first = await agent.run("go", session_id="batch")
    second = await agent.run("again", session_id="batch")
    await agent.record_outcome(second["run_id"], reward=0.0, label="rejected", source="reviewer")

    records = await agent.training_records(session_id="batch")

    assert [r["run_id"] for r in records] == [first["run_id"], second["run_id"]]
    path = tmp_path / "batch.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))
    assert len(path.read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_a_run_with_nothing_recorded_is_not_offered_as_training_data():
    agent = await _agent(ScriptedModel(("call_1", "lookup", '{"key": "a"}')), telemetry_config={"capture": "default"})
    result = await agent.run("go", session_id="private")

    records = await agent.training_records(run_id=result["run_id"])

    assert records == []
