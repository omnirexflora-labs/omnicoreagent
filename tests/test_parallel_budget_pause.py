"""A budget pause in a turn of parallel calls keeps the calls that ran.

The 0.5.0rc5 gate: a 3-call budget, four weather lookups asked at once. Three
ran, the fourth was refused, and the run waited for a top-up. On resume the
model was told the three that ran had lost their results, and the fourth
never ran, though the budgets page promises the pause keeps the work done.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import build_default_policy
from omnicoreagent.governance.models import PolicyBudgets
from test_execute_tool import ScriptedModel, _MODEL


async def _weather_agent(limit: int, ran: list):
    tools = ToolRegistry()

    @tools.register_tool("weather", description="Weather in a city.")
    def weather(city: str) -> dict:
        ran.append(city)
        return {"status": "success", "data": f"sunny in {city}"}

    model = ScriptedModel(
        [(f"w{i}", "weather", f'{{"city": "{c}"}}') for i, c in enumerate(CITIES)],
        "all four are sunny",
    )
    policy = build_default_policy("permissive-dev")
    policy.budgets = PolicyBudgets(request=[{"meter": "tool_calls", "limit": limit}])
    agent = OmniCoreAgent(
        name="weatherman", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                      "governance_config": {"policy": policy}},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


CITIES = ["Lagos", "Oslo", "Lima", "Cairo"]


@pytest.mark.asyncio
async def test_the_calls_that_ran_are_kept_and_the_refused_one_runs_after_a_top_up():
    ran: list[str] = []
    cities = CITIES
    agent = await _weather_agent(3, ran)

    paused = await agent.run("weather please", session_id="par")
    assert paused["status"] == "awaiting_budget", paused
    assert len(ran) == 3 and len(set(ran)) == 3  # which three is not fixed

    await agent.grant_budget(paused["run_id"], amount=5, approver="ops")
    finished = await agent.resume(paused["run_id"])

    assert finished["status"] == "success", finished
    assert sorted(ran) == sorted(cities), "each city once: the three kept, the fourth run now"
    run = await agent.get_run(paused["run_id"])
    assert sorted(c["outcome"] for c in run["tool_calls"]) == ["success"] * 4
    await agent.cleanup()


@pytest.mark.asyncio
async def test_two_refused_calls_make_one_request_and_one_grant_covers_them():
    # The 0.5.0rc5 gate: each refused call made its own pending request, so
    # the documented grant-then-resume failed "still waiting for a budget
    # decision" until the budget was granted twice.
    ran: list[str] = []
    agent = await _weather_agent(2, ran)

    paused = await agent.run("weather please", session_id="par2")
    pending = [r for r in (await agent.get_run(paused["run_id"]))["budget_requests"]
               if r["status"] == "pending"]
    assert len(pending) == 1, pending
    assert pending[0]["shortfall"] == 2, "each refused call counts once"

    await agent.grant_budget(paused["run_id"], amount=5, approver="ops")
    finished = await agent.resume(paused["run_id"])

    assert finished["status"] == "success", finished
    assert sorted(ran) == sorted(CITIES)
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_call_refused_again_after_a_crash_is_not_counted_twice():
    # The 0.5.0rc6 gate: recovery refused the same not-run calls again and
    # added them to the waiting request a second time: two calls read a
    # shortfall of 4, and the default grant allowed twice what was meant.
    from omnicoreagent.core.memory_store.memory_router import MemoryRouter
    from omnicoreagent.core.runs import RunTracker

    tracker = RunTracker(MemoryRouter("in_memory"), run_id="run_b", session_id="s", agent_name="a")
    await tracker.start(None)

    def refusal(call_id):
        return {"request_id": f"r_{call_id}", "key": "request:run_b:total", "scope": "request",
                "meter": "tool_calls", "needed": 1, "shortfall": 1, "status": "pending", "for": call_id}

    for call_id in ("c1", "c2", "c1", "c2"):  # the second pass is the recovery
        waiting = await tracker.add_budget_request(refusal(call_id))

    assert (waiting["needed"], waiting["shortfall"]) == (2, 2)
    assert len(tracker.record["budget_requests"]) == 1
