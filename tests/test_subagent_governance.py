import pytest

from omnicoreagent.core.subagents import SubagentFactory
from omnicoreagent.governance import (
    GovernanceEngine,
    PolicySource,
    UnknownCapabilityError,
    policy_from_mapping,
)
from omnicoreagent.governance.models import PolicyBudget


def _subagent_policy():
    return policy_from_mapping(
        {
            "name": "subagent-policy",
            "mode": "strict",
            "rules": {
                "allow": [
                    {"rule_id": "allow_subagent_spawn", "capability": "subagent.spawn"}
                ]
            },
        }
    )


@pytest.mark.asyncio
async def test_subagent_factory_authorizes_parallel_spawn_before_execution(monkeypatch):
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        governance_engine=GovernanceEngine(_subagent_policy()),
    )
    executed = []

    async def fake_run_subagent(name, role, task, output_path):
        executed.append(name)
        return {
            "status": "success",
            "data": {"subagent_name": name, "output_path": output_path},
            "message": "done",
        }

    monkeypatch.setattr(factory, "run_subagent", fake_run_subagent)

    result = await factory.run_parallel_subagents(
        [
            {
                "name": "api",
                "role": "API reviewer",
                "task": "review API",
                "output_path": "/workspace/reports/api.md",
            },
            {
                "name": "tests",
                "role": "Test reviewer",
                "task": "review tests",
                "output_path": "/workspace/reports/tests.md",
            },
        ]
    )

    assert executed == ["api", "tests"]
    assert result["status"] == "success"
    assert result["data"]["successful"] == 2
    assert factory.governance_engine.policy.budget is None


@pytest.mark.asyncio
async def test_subagent_factory_denies_spawn_before_any_worker_runs(monkeypatch):
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        governance_engine=GovernanceEngine(
            policy_from_mapping(
                {"name": "deny-subagent", "mode": "strict", "rules": {"allow": []}}
            )
        ),
    )
    executed = []

    async def fake_run_subagent(name, role, task, output_path):
        executed.append(name)
        return {"status": "success", "data": {}, "message": "done"}

    monkeypatch.setattr(factory, "run_subagent", fake_run_subagent)

    with pytest.raises(UnknownCapabilityError):
        await factory.run_parallel_subagents(
            [
                {
                    "name": "api",
                    "role": "API reviewer",
                    "task": "review API",
                    "output_path": "/workspace/reports/api.md",
                }
            ]
        )

    assert executed == []


def test_subagent_factory_derives_child_policy_that_denies_recursive_spawn():
    engine = GovernanceEngine(_subagent_policy())
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        agent_config={
            "governance_config": {
                "enabled": True,
                "profile": "strict-production",
            }
        },
        governance_engine=engine,
    )

    config = factory._build_subagent_config(subagent_name="api")
    child_policy = config["governance_config"]["policy"]

    assert config["enable_subagents"] is False
    assert child_policy.provenance.source == PolicySource.INHERITED
    assert child_policy.provenance.parent_policy_id == engine.policy.policy_id
    assert child_policy.metadata["parent_policy_hash"] == (
        engine.policy.provenance.policy_hash
    )
    assert child_policy.rules.deny[0].capability == "subagent.spawn"


def test_subagent_child_policy_does_not_copy_parent_budget_authority():
    policy = _subagent_policy()
    policy.budget = PolicyBudget(max_requests=10, max_cost=2.5)
    engine = GovernanceEngine(policy)
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        governance_engine=engine,
    )

    config = factory._build_subagent_config(subagent_name="api")
    child_budget = config["governance_config"]["policy"].budget

    assert child_budget.max_requests == 0
    assert child_budget.max_cost == 0.0


def test_subagent_governance_reference_exposes_parent_policy_identity():
    engine = GovernanceEngine(_subagent_policy())
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        governance_engine=engine,
    )

    reference = factory._governance_reference()

    assert reference == {
        "parent_policy_id": engine.policy.policy_id,
        "parent_policy_hash": engine.policy.provenance.policy_hash,
    }


@pytest.mark.asyncio
async def test_subagent_authority_request_includes_scope_contract():
    class RecordingEngine:
        def __init__(self):
            self.policy = _subagent_policy()
            self.requests = []

        async def authorize_all(self, requests):
            self.requests.extend(requests)
            return []

    engine = RecordingEngine()
    registry = None
    factory = SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-4o-mini"},
        mcp_tools=[{"name": "filesystem"}],
        local_tools=registry,
        agent_config={"memory_config": {"mode": "sliding_window", "value": 100}},
        governance_engine=engine,
    )

    await factory._authorize_subagent_spawns(
        [
            {
                "name": "api",
                "role": "API reviewer",
                "task": "review API",
                "output_path": "/workspace/reports/api.md",
            }
        ]
    )

    request = engine.requests[0]
    assert request.capability == "subagent.spawn"
    assert request.target.resource == "api"
    assert request.target.path == "/workspace/reports/api.md"
    assert request.metadata["task"] == "review API"
    assert request.metadata["mcp_servers"] == ["filesystem"]
    assert request.metadata["workspace_scope"] == "/workspace/reports/api.md"
    assert request.metadata["memory_scope"]
