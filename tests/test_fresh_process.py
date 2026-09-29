"""A process that only reads or manages runs gets the same answers as the one
that ran them.

Recording real footage of 0.4.3 (2026-09-29): `budget_status(run_id)` returned
`[]` from a second process. Some public methods read the budgets from the inner
agent, which exists only once the agent is initialized, and a process that only
manages runs never ran a query. Every earlier test called these methods on the
agent that had just run, in the same process, so none could see it. These run
real, separate Python processes that share a SQLite store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

COMMON = """
import asyncio, json, os, sys
from omnicoreagent import MemoryRouter
from omnicoreagent.core.budgets import BudgetLedger
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_budget_enforcement import _MODEL, _governance, _tools
from test_budget_pause import _two_turns

DAY = {
    "application_id": "acme",
    "application": [{"meter": "model_cost_usd", "limit": 5.0, "window": "day"}],
    "request": [{"meter": "model_calls", "limit": 1}],
}

def build():
    return OmniCoreAgent(
        name="budget-agent", system_instruction="You spend money carefully.",
        model_config=_MODEL, local_tools=_tools(), memory_router=MemoryRouter("sql"),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                      "governance_config": _governance(DAY)},
    )
"""

FIRST = COMMON + """
async def main():
    agent = build()
    await agent.initialize()
    agent.llm_connection = _two_turns()
    paused = await agent.run("go", session_id="fresh", run_id="run_fresh")
    budgets = agent._build_run_budgets(run_id="run_fresh", session_id=None)
    ((_, key, _),) = list(budgets.limits("model_cost_usd"))
    # What an attempt that died in the middle of a model call leaves behind.
    await BudgetLedger(agent.memory_router).reserve(key, "model_cost_usd", 0.07, limit=5.0, run_id="run_fresh")
    print(json.dumps({"status": paused["status"], "key": key}))
    await agent.cleanup()

asyncio.run(main())
"""

SECOND = COMMON + """
async def main():
    agent = build()   # never runs a query, never initialized by hand
    status = await agent.budget_status("run_fresh")
    held = next((e["reserved"] for e in status if e["scope"] == "application"), None)
    await agent.abandon_run("run_fresh", status="cancelled", reason="nobody will grant it")
    after = await agent.budget_status("run_fresh")
    print(json.dumps({
        "entries": len(status), "held": held,
        "held_after": next((e["reserved"] for e in after if e["scope"] == "application"), None),
        "run": (await agent.get_run("run_fresh"))["status"],
    }))
    await agent.cleanup()

asyncio.run(main())
"""


def _process(tmp_path: Path, name: str, source: str) -> dict:
    script = tmp_path / f"{name}.py"
    script.write_text(textwrap.dedent(source))
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'runs.db'}",
    }
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_fresh_process_sees_a_runs_budgets_and_abandoning_releases_its_hold(tmp_path):
    first = _process(tmp_path, "first", FIRST)
    assert first["status"] == "awaiting_budget"

    second = _process(tmp_path, "second", SECOND)

    assert second["entries"] > 0, "budget_status saw no budgets from a fresh process"
    assert second["held"] == pytest.approx(0.07)
    assert second["held_after"] == 0, "abandoning the run left its hold on the day's counter"
    assert second["run"] == "cancelled"


READINGS = """
# Each reading from its own agent when fresh: one call must not initialize the
# agent for the next and hide what that one does alone.
async def readings(agent_for):
    async def ask(call):
        agent = agent_for()
        try:
            return await call(agent)
        finally:
            if agent_for is not RAN_AGENT:
                await agent.cleanup()
    run = await ask(lambda a: a.get_run("run_fresh"))
    trajectory = await ask(lambda a: a.get_run_trajectory("run_fresh"))
    return {
        "run": [run["status"], run["step"], [(c["tool_name"], c["state"]) for c in run["tool_calls"]]],
        "runs": [(r["run_id"], r["status"]) for r in await ask(lambda a: a.list_runs(session_id="fresh"))],
        "budgets": sorted((e["scope"], e["meter"], e["limit"], round(e["spent"], 6))
                          for e in await ask(lambda a: a.budget_status("run_fresh"))),
        "history": [m["role"] for m in await ask(lambda a: a.get_session_history("fresh"))],
        "trajectory": [trajectory["status"], len(trajectory["segments"]), trajectory["traces_missing"], trajectory["totals"]],
    }
"""

RAN = COMMON + "RAN_AGENT = None\n" + READINGS + """
async def main():
    agent = build()
    await agent.initialize()
    agent.llm_connection = _two_turns()
    await agent.run("go", session_id="fresh", run_id="run_fresh")
    global RAN_AGENT
    RAN_AGENT = lambda: agent
    print(json.dumps(await readings(RAN_AGENT)))
    await agent.cleanup()

asyncio.run(main())
"""

FRESH = COMMON + "RAN_AGENT = None\n" + READINGS + """
async def main():
    print(json.dumps(await readings(build)))

asyncio.run(main())
"""


def test_every_reading_of_a_run_is_the_same_from_a_fresh_process(tmp_path):
    # Whatever a process that only manages runs asks, it gets the answer the
    # process that ran the query would give.
    ran = _process(tmp_path, "ran", RAN)
    fresh = _process(tmp_path, "fresh", FRESH)

    assert ran["budgets"], "the run was budgeted"
    assert fresh == ran
