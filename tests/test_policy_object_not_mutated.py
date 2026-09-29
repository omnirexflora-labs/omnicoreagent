"""R2 (0.5.0rc1 gate): a policy object is the caller's, and stays as they wrote it.

The docs teach building a policy from a profile and adding rules, and setting
budgets in governance_config. Together, the served agent did not start:
"the policy already has budgets". load_policy returned the caller's own object,
stamped it, and the budgets were written into it; OmniServe builds governance
twice (the background manager, then initialize), and the second build saw the
first's budgets. Two agents sharing one module-level policy failed the same way.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import _MODEL

BUDGETS = {"request": [{"meter": "model_calls", "limit": 5}]}


def _policy():
    policy = build_default_policy("permissive-dev")
    policy.rules.ask.append(
        PolicyRule(rule_id="ask_before_refunds", effect=PolicyEffect.ASK, capability="tool.local.call",
                   target={"tool_name": "issue_refund"})
    )
    return policy


@pytest.mark.asyncio
async def test_one_policy_object_serves_two_agents_with_budgets():
    policy = _policy()
    before = policy.provenance.policy_hash

    agents = []
    for name in ("first", "second"):
        agent = OmniCoreAgent(
            name=name, system_instruction="Help.", model_config=_MODEL,
            agent_config={"guardrail_mode": "off", "enable_workspace_files": False,
                          "governance_config": {"policy": policy, "budgets": BUDGETS}},
        )
        await agent.initialize()
        agents.append(agent)

    for agent in agents:
        governed = agent.agent.governance_engine.policy
        assert governed is not policy
        assert governed.budgets is not None
        assert "ask_before_refunds" in [r.rule_id for r in governed.rules.ask]
    assert policy.budgets is None, "the caller's policy object was changed"
    assert policy.provenance.policy_hash == before
    for agent in agents:
        await agent.cleanup()


def test_building_governance_twice_from_one_config_works():
    # What OmniServe does: the background manager builds it, then initialize().
    from omnicoreagent.core.runtime.construction import build_governance_engine

    config = {"governance_config": {"enabled": True, "policy": _policy(), "budgets": BUDGETS}}
    first = build_governance_engine(config)
    second = build_governance_engine(config)
    assert first.policy.budgets is not None and second.policy.budgets is not None
