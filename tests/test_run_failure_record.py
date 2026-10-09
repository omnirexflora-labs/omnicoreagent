"""A run that fails says why on its own record, and under the right category.

The support desk chaos run (2026-10-07) found failed runs whose record held
``error: null``: the cause was only in the trace. One kind, an internal error
while recording a budget change, was also labelled ``provider_error``, which
sent the reader to the model provider for a fault that was ours.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fake_provider import _agent as _fake_agent, running_fake_provider  # noqa: E402

from omnicoreagent.core import llm as llm_module  # noqa: E402
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner  # noqa: E402


@pytest.fixture(autouse=True)
def _no_waiting_between_retries(monkeypatch):
    monkeypatch.setattr(llm_module, "_retry_delay", lambda *args, **kwargs: 0)


def _agent(url, **extra):
    # A key of its own, so the test can tell whether the runtime scrubs it.
    agent = _fake_agent(url, **extra)
    agent.model_config["api_key"] = "fake-key-1234"
    return agent


@pytest.fixture
def provider():
    with running_fake_provider() as url:
        yield url


async def _failed_run(agent, query="Where is order 1042?"):
    result = await agent.run(query, session_id="s")
    record = await agent.get_run(result["run_id"])
    listed = [r for r in await agent.list_runs(session_id="s") if r["run_id"] == result["run_id"]]
    return result, record, listed[0]


@pytest.mark.asyncio
async def test_a_provider_error_that_is_not_retried_away_is_on_the_record(provider):
    httpx.post(f"{provider}/_control", json={"rate_500": 1.0})
    agent = _agent(provider)
    try:
        result, record, listed = await _failed_run(agent)
    finally:
        await agent.cleanup()
    assert result["termination_reason"] == "provider_error"
    assert record["status"] == "failed"
    assert record["error"]["type"] == "InternalServerError"
    assert "500" in record["error"]["message"] or "server" in record["error"]["message"].lower()
    assert record["termination_reason"] == "provider_error"
    assert listed["error"] == record["error"]


@pytest.mark.asyncio
async def test_an_internal_error_outside_the_provider_is_not_a_provider_error(provider, monkeypatch):
    async def broken(self, **kwargs):
        raise RuntimeError("Could not record the budget change for application:desk")

    monkeypatch.setattr(AgentLlmStepRunner, "_record_response", broken)
    agent = _agent(provider)
    try:
        result, record, listed = await _failed_run(agent)
    finally:
        await agent.cleanup()
    assert result["termination_reason"] == "internal_error"
    assert "internal error" in result["response"]
    assert record["status"] == "failed"
    assert record["termination_reason"] == "internal_error"
    assert record["error"]["type"] == "RuntimeError"
    assert "budget change" in record["error"]["message"]
    assert listed["error"] == record["error"]


@pytest.mark.asyncio
async def test_a_raised_runtime_exception_is_on_the_record(provider, monkeypatch):
    agent = _agent(provider)
    await agent.initialize()

    async def explode(*args, **kwargs):
        raise ValueError("the loop broke, key fake-key-1234")

    monkeypatch.setattr(agent.agent, "run", explode)
    try:
        with pytest.raises(ValueError):
            await agent.run("hello", session_id="s", run_id="run_raised")
        record = await agent.get_run("run_raised")
    finally:
        await agent.cleanup()
    assert record["status"] == "failed"
    assert record["error"]["type"] == "ValueError"
    assert "the loop broke" in record["error"]["message"]
    # Errors are redacted on the record as they are in the trace.
    assert "fake-key-1234" not in record["error"]["message"]


@pytest.mark.asyncio
async def test_a_run_that_ends_failed_without_an_exception_still_has_an_error(provider):
    # The step limit ends a run as failed with no exception to record.
    agent = _agent(provider, max_steps=1)
    try:
        result, record, _ = await _failed_run(agent)
    finally:
        await agent.cleanup()
    if record["status"] != "failed":
        pytest.skip("the run ended in success at one step")
    assert record["error"] is not None
    assert record["error"]["message"]
    assert record["termination_reason"] == result["termination_reason"]
