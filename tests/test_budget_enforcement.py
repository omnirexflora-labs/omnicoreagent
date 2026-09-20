"""B3: budgets are enforced where the money is spent.

A model call is reserved at the most it could cost before it is made, and
corrected to its real cost after, so two runs cannot both spend the last
dollar and a crash in between over-counts rather than losing the spend. Tool
calls are counted as they are authorized. Crossing a warning line is recorded;
running out stops the run with ``budget_exhausted``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from omnicoreagent.core.budgets import BudgetLedger, BudgetScope, budget_key
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key", "max_tokens": 1000}

# What one call of the priced model below costs, in dollars.
CALL_COST = 0.02


class PricedModel:
    """A model whose calls have a known price, like a published provider."""

    def __init__(self, *turns, cost_per_call: float | None = CALL_COST):
        self.llm_config = dict(_MODEL)
        self.turns = list(turns)
        self.cost_per_call = cost_per_call
        self.calls = 0

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls += 1
        turn = self.turns.pop(0) if self.turns else ModelTurn(content="done")
        metadata = dict(turn.response_metadata)
        if self.cost_per_call is not None:
            metadata["cost_usd"] = self.cost_per_call
        return replace(
            turn,
            usage=Usage(
                requests=1, request_tokens=100, response_tokens=50, total_tokens=150
            ),
            response_metadata=metadata,
        )

    def estimate_cost(self, usage):
        if self.cost_per_call is None:
            return None
        # Priced per token, so a reservation for the worst case is larger than
        # the call turns out to cost.
        tokens = (usage.request_tokens or 0) + (usage.response_tokens or 0)
        return round(tokens * (CALL_COST / 150), 10)


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 1}

    return tools


def _governance(budgets: dict | None, **extra) -> dict:
    return {
        "enabled": True,
        "policy": {
            "name": "budget-policy",
            "mode": "strict",
            "rules": {"allow": [{"rule_id": "allow_local", "capability": "tool.local.call"}]},
        },
        "budgets": budgets,
        **extra,
    }


async def _agent(model, *, budgets: dict | None = None, name="budget-agent", **config):
    agent = OmniCoreAgent(
        name=name,
        system_instruction="You spend money carefully.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": _governance(budgets),
            **config,
        },
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


async def _usage(agent, scope, identity, window="total") -> dict:
    ledger = BudgetLedger(agent.memory_router)
    return await ledger.usage(budget_key(scope, identity, window))


def _events(trace, event_type):
    return [event for event in trace.events if event.event_type == event_type]


# --- what a run spends is counted --------------------------------------------


@pytest.mark.asyncio
async def test_a_model_call_is_counted_against_the_budgets_that_cover_it():
    agent = await _agent(
        PricedModel(),
        budgets={
            "application_id": "acme",
            "request": [
                {"meter": "model_cost_usd", "limit": 10},
                {"meter": "model_calls", "limit": 10},
                {"meter": "model_tokens", "limit": 10_000},
            ],
            "application": [{"meter": "model_cost_usd", "limit": 100, "window": "day"}],
        },
    )

    result = await agent.run("go", session_id="budget-1")

    spent = await _usage(agent, BudgetScope.REQUEST, result["run_id"])
    assert spent["model_cost_usd"] == pytest.approx(CALL_COST)
    # Only what is budgeted is counted: a meter nobody limits costs no writes.
    assert spent["model_calls"] == 1 and spent["model_tokens"] == 150
    # Every level that covers the call is charged, not only the nearest one.
    daily = await _usage(agent, BudgetScope.APPLICATION, "acme", "day")
    assert daily["model_cost_usd"] == pytest.approx(CALL_COST)
    # Nothing is left held once the call is priced.
    ledger = BudgetLedger(agent.memory_router)
    assert await ledger.reserved(budget_key(BudgetScope.REQUEST, result["run_id"], "total")) == {}


@pytest.mark.asyncio
async def test_a_tool_call_is_counted_as_it_is_authorized():
    agent = await _agent(
        PricedModel(ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),))),
        budgets={"request": [{"meter": "tool_calls", "limit": 10}]},
    )

    result = await agent.run("go", session_id="budget-2")

    spent = await _usage(agent, BudgetScope.REQUEST, result["run_id"])
    assert spent["tool_calls"] == 1


@pytest.mark.asyncio
async def test_nothing_is_counted_when_nothing_is_budgeted():
    agent = await _agent(PricedModel(), budgets=None)

    result = await agent.run("go", session_id="budget-3")

    assert await _usage(agent, BudgetScope.REQUEST, result["run_id"]) == {}


# --- running out stops the work ----------------------------------------------


@pytest.mark.asyncio
async def test_a_run_that_cannot_afford_its_next_model_call_stops_before_making_it():
    model = PricedModel()
    agent = await _agent(
        model,
        budgets={
            "request": [
                {"meter": "model_cost_usd", "limit": 0.001, "on_exhausted": "terminate"}
            ]
        },
    )

    result = await agent.run("go", session_id="budget-4")

    assert result["termination_reason"] == "budget_exhausted"
    assert model.calls == 0, "the call is refused before the money is spent"
    assert "budget" in result["response"].lower()


@pytest.mark.asyncio
async def test_the_application_budget_is_shared_by_every_run():
    budgets = {
        "application_id": "acme",
        "application": [
            {"meter": "model_calls", "limit": 1, "on_exhausted": "terminate"}
        ],
    }
    agent = await _agent(PricedModel(), budgets=budgets)

    first = await agent.run("go", session_id="budget-5")
    second = await agent.run("go again", session_id="budget-6")

    assert first["termination_reason"] != "budget_exhausted"
    assert second["termination_reason"] == "budget_exhausted"
    spent = await _usage(agent, BudgetScope.APPLICATION, "acme")
    assert spent["model_calls"] == 1


@pytest.mark.asyncio
async def test_the_run_that_stopped_says_which_budget_ran_out():
    agent = await _agent(
        PricedModel(),
        budgets={
            "application_id": "acme",
            "application": [
                {"meter": "model_cost_usd", "limit": 0.001, "on_exhausted": "terminate"}
            ],
        },
    )

    result = await agent.run("go", session_id="budget-7")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    [stopped] = _events(trace, "budget_exhausted")
    assert stopped.metadata["scope"] == "application"
    assert stopped.metadata["meter"] == "model_cost_usd"
    assert stopped.metadata["limit"] == 0.001


# --- a warning before the wall -----------------------------------------------


@pytest.mark.asyncio
async def test_crossing_the_warning_line_is_recorded_without_stopping_the_run():
    agent = await _agent(
        PricedModel(),
        budgets={"request": [{"meter": "model_calls", "limit": 2, "warn_at": 0.5}]},
    )

    result = await agent.run("go", session_id="budget-8")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    [warning] = _events(trace, "budget_warning")
    assert warning.metadata["meter"] == "model_calls"
    assert warning.metadata["scope"] == "request"
    assert warning.metadata["remaining"] == pytest.approx(1.0)
    assert result["termination_reason"] != "budget_exhausted"


# --- a model with no published price -----------------------------------------


@pytest.mark.asyncio
async def test_a_model_with_no_price_counts_tokens_and_says_the_cost_is_incomplete():
    agent = await _agent(
        PricedModel(cost_per_call=None),
        budgets={
            "request": [
                {"meter": "model_cost_usd", "limit": 10},
                {"meter": "model_tokens", "limit": 1000},
            ]
        },
    )

    result = await agent.run("go", session_id="budget-9")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    spent = await _usage(agent, BudgetScope.REQUEST, result["run_id"])
    # The call is not refused for having no price: tokens govern it instead.
    assert spent["model_tokens"] == 150 and spent.get("model_cost_usd", 0) == 0
    [incomplete] = _events(trace, "budget_cost_incomplete")
    assert incomplete.metadata["model"] == _MODEL["model"]


# --- the reservation is the most the call could cost --------------------------


@pytest.mark.asyncio
async def test_a_call_holds_the_most_it_could_cost_while_it_runs():
    """The input is counted and the output is capped by ``max_tokens``, so the
    hold is the worst case; the real cost replaces it when the answer lands."""
    from omnicoreagent.core.budgets import estimate_model_call

    model = PricedModel()
    messages = [{"role": "user", "content": "a question worth several tokens"}]

    estimate = estimate_model_call(model, messages, max_output_tokens=1000)

    assert estimate.input_tokens > 0
    assert estimate.output_tokens == 1000
    assert estimate.cost_usd == pytest.approx(
        model.estimate_cost(
            Usage(request_tokens=estimate.input_tokens, response_tokens=1000)
        )
    )
    # More than the call turns out to cost: a hold is never an under-count.
    assert estimate.cost_usd > CALL_COST


@pytest.mark.asyncio
async def test_a_call_whose_price_is_unknown_holds_nothing():
    from omnicoreagent.core.budgets import estimate_model_call

    estimate = estimate_model_call(
        PricedModel(cost_per_call=None), [{"role": "user", "content": "hello"}],
        max_output_tokens=1000,
    )

    assert estimate.cost_usd is None and estimate.input_tokens > 0


# --- the meters that are not the model ---------------------------------------


@pytest.mark.asyncio
async def test_a_sandbox_session_is_charged_for_the_time_it_runs():
    """Providers bill for a session's lifetime, so seconds are charged as they
    pass and again at close, not only at the end."""
    from omnicoreagent.core.budgets import RunBudgets, active_budgets
    from omnicoreagent.core.memory_store.in_memory import InMemoryStore
    from omnicoreagent.governance import GovernanceEngine, PolicyBudgets, build_default_policy
    from omnicoreagent.sandbox import LocalTestSandboxRuntime, SandboxCommandSpec, SandboxExecResult
    from omnicoreagent.sandbox.execution import SandboxExecutionService

    runtime = LocalTestSandboxRuntime(
        commands={"echo": lambda request: SandboxExecResult(exit_code=0, stdout="hi")}
    )
    engine = GovernanceEngine(
        build_default_policy("permissive-dev"),
        sandbox_runtime=runtime,
        allow_test_sandbox_runtime=True,
    )
    ledger = BudgetLedger(InMemoryStore())
    budgets = RunBudgets(
        ledger,
        PolicyBudgets(agent=[{"meter": "sandbox_seconds", "limit": 600}]),
        run_id="run_sandbox",
        agent_name="budget-agent",
    )

    async with active_budgets(budgets):
        service = SandboxExecutionService(engine)
        session = await service.open_session()
        await service.execute(SandboxCommandSpec(command=["echo", "hi"]), session=session)
        await service.close_session(session)

    spent = await ledger.usage(budget_key(BudgetScope.AGENT, "budget-agent", "total"))
    assert spent["sandbox_seconds"] > 0


@pytest.mark.asyncio
async def test_a_delegation_is_charged_to_the_run_that_asked_for_it():
    from omnicoreagent.core.budgets import BudgetExhaustedForRun, RunBudgets, active_budgets
    from omnicoreagent.core.memory_store.in_memory import InMemoryStore
    from omnicoreagent.core.subagents import SubagentFactory
    from omnicoreagent.governance import PolicyBudgets

    ledger = BudgetLedger(InMemoryStore())
    budgets = RunBudgets(
        ledger,
        PolicyBudgets(
            request=[
                {"meter": "subagent_runs", "limit": 2, "on_exhausted": "terminate"}
            ]
        ),
        run_id="run_delegating",
    )
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-5.4-mini"},
        mcp_tools=[],
        local_tools=None,
        agent_config={},
        governance_engine=None,
    )

    async with active_budgets(budgets):
        await factory._authorize_subagent_spawns([{"name": "worker"}])
        with pytest.raises(BudgetExhaustedForRun) as refused:
            await factory._authorize_subagent_spawns(
                [{"name": "worker-2"}, {"name": "worker-3"}]
            )

    assert "subagent_runs" in str(refused.value)
    spent = await ledger.usage(budget_key(BudgetScope.REQUEST, "run_delegating", "total"))
    assert spent["subagent_runs"] == 1, "the refused delegation costs nothing"

