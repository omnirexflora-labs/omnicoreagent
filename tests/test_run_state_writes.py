"""Audit A11: what one request writes to its durable run record, held to a bound.

Counted before this unit: a no-op request saved its run record 6 times and a
tool call 11 times, 4 of those the same record rewritten for every message
stored to history. The durable-runs contract is "saved at step boundaries and
write-ahead around tool calls". An assistant or user message is always
followed by one of those saves before anything can go wrong, so it rides on
it. A tool's result message is the one thing the record has of a completed
call's outcome (the record keeps the call's state, not its result), so it is
still written the moment it exists. Decided with the maintainer.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from test_budget_enforcement import PricedModel, _agent
from test_run_recovery import ProcessDied

ASKS = lambda: PricedModel(ModelTurn(tool_calls=(ToolRequest("c1", "lookup", '{"key": "a"}'),)))  # noqa: E731


class _Saves:
    def __init__(self):
        self.count = 0
        self._original = InMemoryStore.save_run_state

    def __enter__(self):
        counter = self

        async def counted(store, *args, **kwargs):
            counter.count += 1
            return await counter._original(store, *args, **kwargs)

        InMemoryStore.save_run_state = counted
        return self

    def __exit__(self, *_):
        InMemoryStore.save_run_state = self._original


@pytest.mark.asyncio
async def test_a_no_op_request_saves_its_record_at_the_contract_points_only():
    agent = await _agent(PricedModel(), budgets=None)
    await agent.run("warm", session_id="writes-warm")

    with _Saves() as saves:
        agent.llm_connection = PricedModel()
        await agent.run("go", session_id="writes-1")

    # start, history before the first model call, the step, the finish.
    assert saves.count <= 4, f"a no-op request saved its record {saves.count} times"


@pytest.mark.asyncio
async def test_a_tool_call_saves_its_record_at_the_contract_points_and_its_result():
    agent = await _agent(ASKS(), budgets=None)
    await agent.run("warm", session_id="writes-warm-2")

    with _Saves() as saves:
        agent.llm_connection = ASKS()
        await agent.run("go", session_id="writes-2")

    # start, history, step 1, tool started, tool finished, the tool's result
    # message, step 2, finish.
    assert saves.count <= 8, f"a tool call saved its record {saves.count} times"


@pytest.mark.asyncio
async def test_a_completed_calls_result_is_on_the_record_before_the_next_step(monkeypatch):
    """The window that must stay closed: the process dies after a tool has
    completed and before the next step's save. The record must still hold the
    tool's result, or the resumed run could not give it to the model and would
    have to run a call that already had its effect."""
    from omnicoreagent.core import runs

    agent = await _agent(ASKS(), budgets=None)
    original_step = runs.RunTracker.step
    steps = {"n": 0}

    async def dies_on_the_second_step(self, number):
        steps["n"] += 1
        if steps["n"] == 2:
            raise ProcessDied()
        await original_step(self, number)

    monkeypatch.setattr(runs.RunTracker, "step", dies_on_the_second_step)
    with pytest.raises(ProcessDied):
        await agent.run("go", session_id="crash-window", run_id="run_window")
    monkeypatch.setattr(runs.RunTracker, "step", original_step)

    record = await agent.get_run("run_window")
    [call] = record["tool_calls"]
    assert call["state"] == "completed"
    assert any(m["role"] == "tool" for m in record["context"]["messages"]), (
        "the completed call's result is not on the record"
    )
