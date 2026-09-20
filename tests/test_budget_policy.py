"""B2: a budget is part of the policy, so it cannot be widened at runtime.

Budgets say what a request, a session, an agent, or an application may spend.
They live in the policy envelope, are covered by its hash, and nothing is
budgeted unless the application says so. The old ``policy.budget`` keeps
working and is expressed in the new shape.
"""

from __future__ import annotations

import pytest

from omnicoreagent.governance import (
    BudgetLimit,
    PolicyBudgets,
    PolicyEnvelope,
    load_policy,
    policy_hash,
)


def _policy(**kwargs) -> PolicyEnvelope:
    return PolicyEnvelope(name="budget-policy", **kwargs)


# --- nothing is budgeted unless it is asked for -------------------------------


def test_a_policy_budgets_nothing_by_default():
    policy = _policy()

    assert policy.budgets is None
    assert PolicyBudgets().limits_for("request") == []


def test_budgets_are_read_per_scope_with_their_window_and_what_to_do_when_spent():
    policy = _policy(
        budgets={
            "application_id": "acme",
            "application": [{"meter": "model_cost_usd", "limit": 200, "window": "day"}],
            "request": [
                {
                    "meter": "model_cost_usd",
                    "limit": 2,
                    "on_exhausted": "terminate",
                    "warn_at": 0.5,
                }
            ],
        }
    )

    [daily] = policy.budgets.limits_for("application")
    assert (daily.meter, daily.limit, daily.window) == ("model_cost_usd", 200.0, "day")
    # A budget that runs out pauses for a person by default, and warns at 80%.
    assert (daily.on_exhausted, daily.warn_at) == ("pause", 0.8)
    [per_request] = policy.budgets.limits_for("request")
    assert (per_request.window, per_request.on_exhausted, per_request.warn_at) == (
        "total",
        "terminate",
        0.5,
    )
    assert policy.budgets.limits_for("session") == []


# --- a budget that cannot be read is refused, not guessed ---------------------


@pytest.mark.parametrize(
    "limit, message",
    [
        ({"meter": "vibes", "limit": 1}, "meter"),
        ({"meter": "model_cost_usd", "limit": 1, "window": "fortnight"}, "window"),
        ({"meter": "model_cost_usd", "limit": 0}, "limit"),
        ({"meter": "model_cost_usd", "limit": -3}, "limit"),
        ({"meter": "model_cost_usd", "limit": 1, "warn_at": 1.5}, "warn_at"),
        ({"meter": "model_cost_usd", "limit": 1, "on_exhausted": "shrug"}, "on_exhausted"),
    ],
)
def test_a_budget_that_cannot_be_read_is_refused(limit, message):
    with pytest.raises(ValueError, match=message):
        _policy(budgets={"request": [limit]})


def test_an_application_budget_needs_to_say_which_application():
    with pytest.raises(ValueError, match="application_id"):
        _policy(budgets={"application": [{"meter": "model_cost_usd", "limit": 10}]})

    # Every other scope is identified by the run, session, or agent itself.
    assert _policy(budgets={"session": [{"meter": "tool_calls", "limit": 10}]}).budgets


# --- budgets are authority, so the hash covers them ---------------------------


def test_changing_a_budget_changes_the_policy_hash():
    without = policy_hash(_policy())
    with_budget = policy_hash(
        _policy(budgets={"request": [{"meter": "model_cost_usd", "limit": 2}]})
    )
    widened = policy_hash(
        _policy(budgets={"request": [{"meter": "model_cost_usd", "limit": 200}]})
    )

    assert len({without, with_budget, widened}) == 3


def test_the_run_record_shows_the_budgets_that_governed_it():
    from omnicoreagent.governance.snapshots import policy_snapshot_from_policy

    policy = _policy(
        budgets={
            "application_id": "acme",
            "application": [{"meter": "model_cost_usd", "limit": 200, "window": "day"}],
        }
    )

    snapshot = policy_snapshot_from_policy(policy)

    assert snapshot["budgets"]["application_id"] == "acme"
    assert snapshot["budgets"]["application"] == [
        {
            "meter": "model_cost_usd",
            "limit": 200.0,
            "window": "day",
            "warn_at": 0.8,
            "on_exhausted": "pause",
        }
    ]


# --- what was already there keeps working ------------------------------------


def test_the_old_policy_budget_is_expressed_in_the_new_shape():
    policy = _policy(budget={"max_requests": 5, "max_cost": 2.5})

    limits = {limit.meter: limit for limit in policy.budgets.limits_for("agent")}

    assert limits["tool_calls"].limit == 5.0
    assert limits["model_cost_usd"].limit == 2.5
    # The old field is left alone; the new shape is how it is enforced.
    assert policy.budget.max_requests == 5


def test_an_old_budget_of_zero_stays_on_the_old_field():
    """Zero is how a subagent policy is narrowed today: it refuses the request,
    rather than becoming a budget that lets a run spend nothing."""
    policy = _policy(budget={"max_requests": 0, "max_cost": 0.0})

    assert policy.budgets is None


def test_a_budget_written_in_the_new_shape_wins_over_the_old_field():
    policy = _policy(
        budget={"max_requests": 5},
        budgets={"agent": [{"meter": "tool_calls", "limit": 9}]},
    )

    [limit] = policy.budgets.limits_for("agent")
    assert limit.limit == 9.0


# --- the convenience for applications that use no policy file -----------------


def test_an_application_can_set_budgets_without_a_policy_file():
    from omnicoreagent.core.runtime.construction import build_governance_engine

    engine = build_governance_engine(
        {
            "governance_config": {
                "enabled": True,
                "profile": "permissive-dev",
                "budgets": {
                    "application_id": "acme",
                    "application": [{"meter": "model_cost_usd", "limit": 200, "window": "day"}],
                },
            }
        }
    )

    [limit] = engine.policy.budgets.limits_for("application")
    assert limit.limit == 200.0
    # The budget is part of the policy it hashed, not something added later.
    assert engine.policy.provenance.policy_hash == policy_hash(engine.policy)


def test_budgets_cannot_be_set_in_two_places_at_once():
    policy = load_policy(
        policy={
            "name": "file-policy",
            "budgets": {"request": [{"meter": "model_cost_usd", "limit": 1}]},
        }
    )
    assert policy.budgets is not None

    from omnicoreagent.core.runtime.construction import build_governance_engine

    with pytest.raises(ValueError, match="budgets"):
        build_governance_engine(
            {
                "governance_config": {
                    "enabled": True,
                    "policy": {
                        "name": "file-policy",
                        "budgets": {"request": [{"meter": "model_cost_usd", "limit": 1}]},
                    },
                    "budgets": {"request": [{"meter": "model_cost_usd", "limit": 50}]},
                }
            }
        )


def test_a_budget_limit_can_be_built_directly():
    limit = BudgetLimit(meter="sandbox_seconds", limit=600, window="month")

    assert (limit.meter, limit.limit, limit.window) == ("sandbox_seconds", 600.0, "month")
