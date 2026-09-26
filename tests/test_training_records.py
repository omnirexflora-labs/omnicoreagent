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


@pytest.mark.asyncio
async def test_a_paused_and_resumed_run_is_one_record_with_every_segment_and_its_reward(tmp_path):
    """A run that waited for a person is two traces: the part before the pause
    and the part after it. A trainer needs one run, whole, with the reward that
    arrived after it finished — not a record per trace, and not the paused part
    as if it were a finished run."""
    from test_durable_runs_end_to_end import _agent as _billing_agent
    from test_durable_runs_end_to_end import _tools as _billing_tools
    from test_run_suspend import RecordingModel

    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "invoice sent",
    )
    agent = await _billing_agent(model, _billing_tools(tmp_path / "ledger", {"armed": False}))

    paused = await agent.run("draft and send the invoice", session_id="billing", run_id="run_rl")
    assert paused["status"] == "awaiting_approval"
    assert await agent.training_records(run_id="run_rl") == [], "an unfinished run is not training data"

    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_rl", approval["approval_id"], decision="approve", approver="alice")
    finished = await agent.resume("run_rl")
    assert finished["response"] == "invoice sent"
    await agent.record_outcome("run_rl", reward=1.0, label="paid", source="billing")

    by_run = await agent.training_records(run_id="run_rl")
    by_session = await agent.training_records(session_id="billing")

    assert by_run == by_session
    (record,) = by_run
    run = await agent.get_run("run_rl")
    assert record["run_id"] == "run_rl" and record["status"] == "completed"
    assert record["trace_ids"] == run["trace_ids"] and len(record["trace_ids"]) == 2
    called = [call["name"] for step in record["steps"] for call in step["tool_calls"]]
    assert called[:1] == ["draft"] and "send_invoice" in called, "both segments' steps"
    assert {step["segment"] for step in record["steps"]} == {0, 1}
    assert record["steps"][-1]["response"]["content"] == "invoice sent"
    assert record["request"] == "draft and send the invoice"
    assert [(o["reward"], o["label"]) for o in record["outcomes"]] == [(1.0, "paid")]


@pytest.mark.asyncio
async def test_an_evaluator_records_outcomes_without_a_model_key(monkeypatch, tmp_path):
    """Round two: an evaluator process that only reads runs and reports
    rewards raised `LLM_API_KEY not found`, though it calls no model."""
    from omnicoreagent import OmniCoreAgent

    ran = await _agent(ScriptedModel(("call_1", "lookup", '{"key": "a"}')))
    result = await ran.run("go", session_id="eval")

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    evaluator = OmniCoreAgent(
        name="tool-agent",
        system_instruction="x",
        model_config={"provider": "openai", "model": "gpt-5.4-mini"},
        memory_router=ran.memory_router,
        telemetry_store=ran.telemetry_store,
    )
    assert (await evaluator.get_run(result["run_id"]))["status"] == "completed"
    await evaluator.record_outcome(result["run_id"], reward=1.0, source="ci")
    (record,) = await evaluator.training_records(run_id=result["run_id"])
    assert [o["reward"] for o in record["outcomes"]] == [1.0]
