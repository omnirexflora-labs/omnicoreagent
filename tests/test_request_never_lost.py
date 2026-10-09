"""A user's request is never lost when the memory store fails at the start.

The support desk chaos run (2026-10-07), run_69673b9e: Postgres was restarting,
and the run's first memory read failed before the user's message was saved. The
run stayed `running`, was resumed after its lease lapsed, and finished
`completed`, answering a refund request that no record held. The rules these
tests hold: the request is on the run's record from its first save; a resume
always has it; a run that cannot have it fails instead of answering; and a
read that fails for a moment is tried again.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.runtime import omnicore_agent as agent_module
from omnicoreagent.core.runs import RunRequestLost, update_from_outside

from test_run_recovery import ProcessDied, _agent
from test_run_suspend import RecordingModel


class StoreDown(Exception):
    """Stands in for psycopg2.OperationalError: the database is shutting down."""


def _tools():
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    return ToolRegistry()


def _fail_reads(agent, *, times, error=StoreDown("the database system is shutting down")):
    """Make the memory store's reads fail ``times`` times, then work."""
    real = agent.memory_router.get_messages
    state = {"left": times, "calls": 0}

    async def get_messages(*args, **kwargs):
        state["calls"] += 1
        if state["left"]:
            state["left"] -= 1
            raise error
        return await real(*args, **kwargs)

    agent.memory_router.get_messages = get_messages
    return state


def _user_texts(call):
    return [m["content"] for m in call if m.get("role") == "user"]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(agent_module, "STORE_READ_RETRY_DELAYS", (0, 0))


@pytest.mark.asyncio
async def test_the_request_is_on_the_record_from_the_first_save():
    agent = await _agent(RecordingModel("done"), _tools())
    seen = {}
    real = agent.memory_router.get_messages

    async def get_messages(*args, **kwargs):
        # The first memory read is before any message is stored.
        seen["record"] = await agent.get_run("run_a")
        return await real(*args, **kwargs)

    agent.memory_router.get_messages = get_messages
    await agent.run("refund order 77", session_id="s", run_id="run_a")

    assert seen["record"]["request"]["content"] == "refund order 77"


@pytest.mark.asyncio
async def test_a_read_that_fails_for_a_moment_is_tried_again():
    model = RecordingModel("refunded")
    agent = await _agent(model, _tools())
    state = _fail_reads(agent, times=1)

    result = await agent.run("refund order 77", session_id="s", run_id="run_b")

    assert result["status"] == "success" and state["calls"] >= 2
    assert "refund order 77" in _user_texts(model.calls[0])[-1]


@pytest.mark.asyncio
async def test_a_store_that_stays_down_fails_the_run_with_its_error():
    model = RecordingModel("never reached")
    agent = await _agent(model, _tools())
    _fail_reads(agent, times=99)

    with pytest.raises(StoreDown):
        await agent.run("refund order 77", session_id="s", run_id="run_c")

    record = await agent.get_run("run_c")
    assert record["status"] == "failed"
    assert record["error"]["type"] == "StoreDown"
    assert "shutting down" in record["error"]["message"]
    assert model.calls == [], "the model answered a request it never had"


@pytest.mark.asyncio
async def test_a_resume_after_the_process_died_has_the_request(tmp_path):
    # The incident: the process (or the store) went away before the user's
    # message was saved, so the run was left `running` with nothing recorded.
    model = RecordingModel("refunded order 77")
    agent = await _agent(model, _tools())
    _fail_reads(agent, times=1, error=ProcessDied())
    with pytest.raises(ProcessDied):
        await agent.run("refund order 77", session_id="s", run_id="run_d")
    crashed = await agent.get_run("run_d")
    assert crashed["status"] == "running" and crashed["context"]["messages"] == []
    await asyncio.sleep(1.2)

    result = await agent.resume("run_d")

    assert result["status"] == "success"
    assert any("refund order 77" in text for text in _user_texts(model.calls[0]))
    # The session's history has the request too, so the next turn sees it.
    history = await agent.memory_router.get_messages("s", agent.name)
    assert [m["content"] for m in history if m["role"] == "user"] == ["refund order 77"]


@pytest.mark.asyncio
async def test_a_resume_that_has_no_request_refuses_instead_of_answering(tmp_path):
    model = RecordingModel("an answer to nothing")
    agent = await _agent(model, _tools())
    _fail_reads(agent, times=1, error=ProcessDied())
    with pytest.raises(ProcessDied):
        await agent.run("refund order 77", session_id="s", run_id="run_e")

    def forget(record):
        # A record from before the request was kept on it.
        record.pop("request", None)

    await update_from_outside(agent.memory_router, "run_e", forget)
    await asyncio.sleep(1.2)

    with pytest.raises(RunRequestLost):
        await agent.resume("run_e")

    record = await agent.get_run("run_e")
    assert record["status"] == "failed"
    assert record["error"]["type"] == "RunRequestLost"
    assert model.calls == []
    assert json.dumps(record["error"])  # a message a person can act on
