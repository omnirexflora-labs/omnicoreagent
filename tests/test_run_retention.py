"""Run-record retention (RR2): finished run records are pruned after
`run_retention_days` (30 by default, the maintainer's choice); `None` keeps
every record; a run still waiting for a person is never pruned."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omnicoreagent import OmniCoreAgent
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


async def _agent(**config) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name="retention",
        system_instruction="x",
        model_config=MODEL,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **config},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel()
    return agent


async def _seed(agent, run_id, status, days_ago):
    started = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    await agent.memory_router.save_run_state(
        {"run_id": run_id, "session_id": "s", "status": status, "step": 1, "created_at": started},
        expected_version=None,
    )


@pytest.mark.asyncio
async def test_finished_runs_older_than_the_window_are_pruned_and_waiting_ones_kept():
    agent = await _agent()  # the default: 30 days
    await _seed(agent, "old_done", "completed", 45)
    await _seed(agent, "old_waiting", "awaiting_approval", 45)
    await _seed(agent, "recent_done", "completed", 3)

    summary = await agent.prune_runs()

    assert summary["runs_removed"] == 1 and summary["retention_days"] == 30
    assert await agent.get_run("old_done") is None
    assert await agent.get_run("old_waiting") is not None
    assert await agent.get_run("recent_done") is not None


@pytest.mark.asyncio
async def test_none_keeps_every_record_forever():
    agent = await _agent(run_retention_days=None)
    await _seed(agent, "ancient", "completed", 3650)

    summary = await agent.prune_runs()

    assert summary["runs_removed"] == 0 and summary["retention_days"] is None
    assert await agent.get_run("ancient") is not None


@pytest.mark.asyncio
async def test_the_window_is_the_users_to_set():
    agent = await _agent(run_retention_days=7)
    await _seed(agent, "ten_days", "failed", 10)
    await _seed(agent, "five_days", "failed", 5)

    assert (await agent.prune_runs())["runs_removed"] == 1
    assert await agent.get_run("five_days") is not None


@pytest.mark.asyncio
async def test_retention_runs_once_by_itself_and_is_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = await _agent()
    await _seed(agent, "old_done", "completed", 45)

    await agent.run("go")
    await agent.run("again")

    assert await agent.get_run("old_done") is None
    status = agent.telemetry_retention_status()
    assert status["runs"]["last_cleanup"]["runs_removed"] == 1
    assert status["runs"]["automatic_runs"] == 1, "once per agent, not per run"


def test_the_window_must_be_a_number_of_days_or_none():
    with pytest.raises(ValueError, match="run_retention_days"):
        OmniCoreAgent(name="a", system_instruction="x", model_config=MODEL, agent_config={"run_retention_days": -1})


@pytest.mark.asyncio
async def test_a_run_whose_traces_were_removed_says_so_instead_of_reading_as_zero(
    tmp_path, monkeypatch
):
    # Traces are kept 7 days, run records 30: in between, a run's story has
    # its record but not its traces. It must not read as a run that used
    # nothing; the record's usage is still there.
    monkeypatch.chdir(tmp_path)
    agent = await _agent()
    result = await agent.run("go")
    kept = await agent.get_run_trajectory(result["run_id"])
    assert kept["traces_missing"] == 0
    assert all(segment["trace_kept"] for segment in kept["segments"])

    await agent.memory_router.save_run_state(
        {
            "run_id": "old",
            "session_id": "s",
            "status": "completed",
            "step": 1,
            "trace_ids": ["trace_removed_by_retention"],
            "usage": {"total_tokens": 1200},
        },
        expected_version=None,
    )
    story = await agent.get_run_trajectory("old")

    assert story["traces_missing"] == 1
    assert story["segments"][0]["trace_kept"] is False
    assert story["totals"] == {}  # unknown, not zero
    assert story["usage"] == {"total_tokens": 1200}
