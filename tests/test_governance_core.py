import json
from datetime import timedelta

import pytest

from omnicoreagent.core.telemetry import TelemetryActor, TelemetryEvent
from omnicoreagent.governance import (
    ApprovalExpiredError,
    ApprovalRequiredError,
    ApprovalResult,
    AuthorityRequest,
    BudgetExceededError,
    GovernanceEngine,
    PolicyConstraints,
    PolicyDeniedError,
    PolicyEvaluationError,
    PolicyEffect,
    PolicyEnvelope,
    PolicyEvaluator,
    PolicyLoadError,
    PolicyMode,
    PolicyRule,
    PolicyRuleSet,
    PolicySource,
    ReasonCode,
    SandboxRequiredError,
    StaticApprovalResolver,
    UnknownCapabilityError,
    build_default_policy,
    canonical_policy_payload,
    load_policy,
    load_policy_file,
    policy_from_mapping,
    policy_hash,
)
from omnicoreagent.governance.models import utc_now


class BrokenEvaluator:
    def evaluate(self, policy, request):
        raise PolicyEvaluationError("boom")


class RawBrokenEvaluator:
    def evaluate(self, policy, request):
        raise ValueError("raw boom")


class ExpiredApprovalResolver:
    async def resolve(self, request):
        return ApprovalResult(
            approved=True,
            approval_id=request.approval_id,
            resolved_by="expired",
            resolved_at=utc_now() + timedelta(seconds=10),
        )


def _policy(*, mode=PolicyMode.STRICT):
    return policy_from_mapping(
        {
            "name": "test-policy",
            "mode": mode.value if isinstance(mode, PolicyMode) else mode,
            "rules": {
                "deny": [
                    {
                        "rule_id": "deny_secret",
                        "capability": "secret.read",
                    },
                    {
                        "rule_id": "deny_external_post",
                        "capability": "network.http.post",
                        "conditions": {"host": "*.blocked.test"},
                    },
                ],
                "ask": [
                    {
                        "rule_id": "ask_shell",
                        "capability": "process.*",
                    }
                ],
                "allow": [
                    {
                        "rule_id": "allow_workspace",
                        "capability": "workspace.files.*",
                    },
                    {
                        "rule_id": "allow_network_get",
                        "capability": "network.http.get",
                    },
                ],
            },
        }
    )


def test_policy_evaluator_precedence_denies_before_ask_and_allow():
    policy = policy_from_mapping(
        {
            "name": "precedence",
            "mode": "strict",
            "rules": {
                "deny": [{"rule_id": "deny_all_network", "capability": "network.*"}],
                "ask": [{"rule_id": "ask_post", "capability": "network.http.post"}],
                "allow": [{"rule_id": "allow_post", "capability": "network.http.post"}],
            },
        }
    )
    decision = PolicyEvaluator().evaluate(
        policy,
        AuthorityRequest(capability="network.http.post"),
    )

    assert decision.effect == PolicyEffect.DENY
    assert decision.reason_code == ReasonCode.MATCHED_DENY
    assert decision.matched_rule_ids == ["deny_all_network"]


def test_policy_evaluator_matches_conditions_and_targets():
    decision = PolicyEvaluator().evaluate(
        _policy(),
        AuthorityRequest(
            capability="network.http.post",
            host="api.blocked.test",
            method="POST",
        ),
    )

    assert decision.effect == PolicyEffect.DENY
    assert decision.matched_rule_ids == ["deny_external_post"]


def test_unknown_capability_behavior_depends_on_policy_mode():
    evaluator = PolicyEvaluator()

    strict = evaluator.evaluate(
        _policy(mode=PolicyMode.STRICT),
        AuthorityRequest(capability="unknown.surface.call"),
    )
    interactive = evaluator.evaluate(
        _policy(mode=PolicyMode.INTERACTIVE),
        AuthorityRequest(capability="unknown.surface.call"),
    )
    permissive = evaluator.evaluate(
        _policy(mode=PolicyMode.PERMISSIVE),
        AuthorityRequest(capability="unknown.surface.call"),
    )

    assert strict.effect == PolicyEffect.DENY
    assert strict.reason_code == ReasonCode.UNKNOWN_CAPABILITY
    assert interactive.effect == PolicyEffect.ASK
    assert interactive.reason_code == ReasonCode.UNKNOWN_CAPABILITY
    assert permissive.effect == PolicyEffect.ALLOW


def test_budget_exceeded_denies_before_rule_matching():
    policy = _policy()
    policy.budget = {"max_requests": 1, "used_requests": 1}
    policy.__post_init__()

    decision = PolicyEvaluator().evaluate(
        policy,
        AuthorityRequest(capability="workspace.files.write"),
    )

    assert decision.effect == PolicyEffect.DENY
    assert decision.reason_code == ReasonCode.BUDGET_EXCEEDED


def test_policy_hash_is_canonical_across_key_order_defaults_and_generated_fields():
    first = policy_from_mapping(
        {
            "name": "hash",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "rule_id": "allow_workspace",
                        "capability": "workspace.files.read",
                        "conditions": {},
                    }
                ]
            },
        }
    )
    second = policy_from_mapping(
        {
            "mode": "strict",
            "name": "hash",
            "policy_id": "caller-supplied",
            "rules": {
                "allow": [
                    {
                        "capability": "workspace.files.read",
                        "rule_id": "allow_workspace",
                    }
                ],
            },
        }
    )
    third = policy_from_mapping(
        {
            "name": "hash",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "capability": "workspace.files.read",
                        "rule_id": "allow_workspace",
                    }
                ],
            },
        }
    )

    assert policy_hash(first) == policy_hash(third)
    assert policy_hash(second) != policy_hash(third)
    assert "loaded_at" not in json.dumps(canonical_policy_payload(first))


def test_policy_loading_uses_default_when_no_file_exists(tmp_path):
    policy = load_policy(project_root=tmp_path)

    assert isinstance(policy, PolicyEnvelope)
    assert policy.profile.value == "interactive-dev"
    assert policy.provenance.source == PolicySource.DEFAULT


def test_auto_discovered_policy_cannot_broaden_default_baseline(tmp_path):
    path = tmp_path / "omnicoreagent.policy.json"
    path.write_text(
        json.dumps(
            {
                "name": "broadening",
                "mode": "permissive",
                "rules": {
                    "allow": [
                        {
                            "rule_id": "allow_network",
                            "capability": "network.http.get",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PolicyLoadError, match="broadens"):
        load_policy(project_root=tmp_path)


def test_auto_discovered_policy_preserves_baseline_denies_and_asks(tmp_path):
    path = tmp_path / "omnicoreagent.policy.json"
    path.write_text(
        json.dumps(
            {
                "name": "narrow",
                "mode": "permissive",
                "rules": {
                    "allow": [
                        {
                            "rule_id": "allow_workspace_read",
                            "capability": "workspace.files.read",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    policy = load_policy(project_root=tmp_path)
    evaluator = PolicyEvaluator()

    secret = evaluator.evaluate(policy, AuthorityRequest(capability="secret.read"))
    process = evaluator.evaluate(policy, AuthorityRequest(capability="process.exec"))
    workspace = evaluator.evaluate(
        policy,
        AuthorityRequest(capability="workspace.files.read"),
    )

    assert policy.mode == PolicyMode.INTERACTIVE
    assert secret.effect == PolicyEffect.DENY
    assert process.effect == PolicyEffect.ASK
    assert workspace.effect == PolicyEffect.ALLOW


def test_policy_loading_rejects_workspace_policy_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "policy.json"
    path.write_text('{"name": "bad", "rules": {}}', encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="agent-writable"):
        load_policy_file(path, project_root=tmp_path)


def test_policy_loading_rejects_path_outside_project_root(tmp_path):
    outside = tmp_path.parent / "outside-policy.json"
    outside.write_text('{"name": "bad", "rules": {}}', encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="escapes trusted project root"):
        load_policy_file(outside, project_root=tmp_path)


def test_policy_loading_rejects_auto_discovered_symlink(tmp_path):
    target = tmp_path / "real-policy.json"
    target.write_text('{"name": "real", "rules": {}}', encoding="utf-8")
    link = tmp_path / "omnicoreagent.policy.json"
    link.symlink_to(target)

    with pytest.raises(PolicyLoadError, match="symlink"):
        load_policy(project_root=tmp_path)


def test_yaml_policy_loading_is_not_enabled_in_phase_one(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("name: yaml-policy\nrules: {}\n", encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="YAML policy loading is not enabled"):
        load_policy_file(path, project_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "not-a-mode"},
        {
            "rules": {
                "allow": [
                    {
                        "rule_id": "bad_effect",
                        "effect": "maybe",
                        "capability": "workspace.files.read",
                    }
                ]
            }
        },
        {"rules": {"allow": [{"rule_id": "missing_capability"}]}},
        {
            "rules": {
                "allow": [
                    {
                        "rule_id": "bad_constraints",
                        "capability": "workspace.files.read",
                        "constraints": {"unknown": True},
                    }
                ]
            }
        },
    ],
)
def test_policy_file_loading_wraps_invalid_policy_construction(tmp_path, payload):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PolicyLoadError) as exc:
        load_policy_file(path, project_root=tmp_path)

    assert exc.value.__cause__ is not None


@pytest.mark.asyncio
async def test_governance_engine_authorize_allows_safe_request():
    engine = GovernanceEngine(_policy())

    decision = await engine.authorize(
        AuthorityRequest(capability="workspace.files.write"),
    )

    assert decision.effect == PolicyEffect.ALLOW
    assert decision.matched_rule_ids == ["allow_workspace"]


@pytest.mark.asyncio
async def test_governance_engine_attaches_missing_policy_hash():
    policy = PolicyEnvelope(
        name="direct",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(
            allow=[
                PolicyRule(
                    rule_id="allow_workspace",
                    effect=PolicyEffect.ALLOW,
                    capability="workspace.files.read",
                )
            ]
        ),
    )
    assert policy.provenance.policy_hash == ""

    decision = await GovernanceEngine(policy).authorize(
        AuthorityRequest(capability="workspace.files.read")
    )

    assert policy.provenance.policy_hash
    assert decision.policy_hash == policy.provenance.policy_hash


@pytest.mark.asyncio
async def test_governance_engine_consumes_budget_after_allowed_attempt():
    policy = _policy()
    policy.budget = {"max_requests": 1, "used_requests": 0}
    policy.__post_init__()
    engine = GovernanceEngine(policy)

    await engine.authorize(AuthorityRequest(capability="workspace.files.write"))

    with pytest.raises(BudgetExceededError):
        await engine.authorize(AuthorityRequest(capability="workspace.files.write"))


@pytest.mark.asyncio
async def test_governance_engine_denies_unknown_capability_with_stable_error():
    engine = GovernanceEngine(_policy())

    with pytest.raises(UnknownCapabilityError) as exc:
        await engine.authorize(AuthorityRequest(capability="unknown.call"))

    assert exc.value.code == "unknown_capability"
    assert exc.value.message == "Unknown capability denied by strict policy."
    assert exc.value.metadata["reason_code"] == "unknown_capability"


@pytest.mark.asyncio
async def test_governance_engine_raises_budget_exceeded_with_stable_error():
    policy = _policy()
    policy.budget = {"max_requests": 1, "used_requests": 1}
    policy.__post_init__()
    engine = GovernanceEngine(policy)

    with pytest.raises(BudgetExceededError) as exc:
        await engine.authorize(AuthorityRequest(capability="workspace.files.write"))

    assert exc.value.code == "budget_exceeded"


@pytest.mark.asyncio
async def test_governance_engine_fails_closed_on_policy_evaluation_error():
    engine = GovernanceEngine(_policy(), evaluator=BrokenEvaluator())

    with pytest.raises(PolicyEvaluationError) as exc:
        await engine.authorize(AuthorityRequest(capability="workspace.files.write"))

    assert exc.value.code == "policy_evaluation_error"


@pytest.mark.asyncio
async def test_governance_engine_wraps_raw_evaluator_failures():
    engine = GovernanceEngine(_policy(), evaluator=RawBrokenEvaluator())

    with pytest.raises(PolicyEvaluationError) as exc:
        await engine.authorize(AuthorityRequest(capability="workspace.files.write"))

    assert exc.value.code == "policy_evaluation_error"
    assert exc.value.metadata["reason_code"] == "policy_error"


@pytest.mark.asyncio
async def test_governance_engine_raises_approval_required_without_resolver():
    engine = GovernanceEngine(_policy())

    with pytest.raises(ApprovalRequiredError) as exc:
        await engine.authorize(AuthorityRequest(capability="process.exec"))

    assert exc.value.metadata["reason_code"] == "approval_required"


@pytest.mark.asyncio
async def test_governance_engine_uses_approval_resolver_for_ask_decision():
    engine = GovernanceEngine(
        _policy(),
        approval_resolver=StaticApprovalResolver(
            approved=True,
            resolved_by="test",
            reason="approved in test",
        ),
    )

    decision = await engine.authorize(AuthorityRequest(capability="process.exec"))

    assert decision.effect == PolicyEffect.ALLOW
    assert decision.approval_id is not None
    assert decision.reason == "approved in test"


@pytest.mark.asyncio
async def test_governance_engine_blocks_static_high_risk_approval_by_default():
    engine = GovernanceEngine(
        _policy(),
        approval_resolver=StaticApprovalResolver(approved=True),
    )

    with pytest.raises(ApprovalRequiredError, match="Static approvals"):
        await engine.authorize(
            AuthorityRequest(capability="process.exec", risk_level="high")
        )


@pytest.mark.asyncio
async def test_governance_engine_rejects_expired_approval():
    policy = PolicyEnvelope(
        name="approval-expiry",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(
            ask=[
                PolicyRule(
                    rule_id="ask_process",
                    effect=PolicyEffect.ASK,
                    capability="process.exec",
                    constraints=PolicyConstraints(approval_expires_seconds=1),
                )
            ]
        ),
    )
    engine = GovernanceEngine(policy, approval_resolver=ExpiredApprovalResolver())

    with pytest.raises(ApprovalExpiredError) as exc:
        await engine.authorize(AuthorityRequest(capability="process.exec"))

    assert exc.value.metadata["reason_code"] == "expired_policy"


@pytest.mark.asyncio
async def test_governance_engine_raises_policy_denied_for_matched_deny():
    engine = GovernanceEngine(_policy())

    with pytest.raises(PolicyDeniedError):
        await engine.authorize(AuthorityRequest(capability="secret.read"))


@pytest.mark.asyncio
async def test_governance_engine_enforces_sandbox_constraint():
    policy = PolicyEnvelope(
        name="sandbox",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(
            allow=[
                PolicyRule(
                    rule_id="allow_sandboxed_process",
                    effect=PolicyEffect.ALLOW,
                    capability="process.exec",
                    constraints=PolicyConstraints(sandbox_required=True),
                )
            ]
        ),
    )
    engine = GovernanceEngine(policy)

    with pytest.raises(SandboxRequiredError) as exc:
        await engine.authorize(AuthorityRequest(capability="process.exec"))

    assert exc.value.metadata["reason_code"] == "sandbox_required"


def test_governance_telemetry_event_types_are_registered():
    for event_type in (
        "policy_request_created",
        "policy_decision_allow",
        "policy_decision_ask",
        "policy_decision_deny",
        "approval_request_created",
        "approval_resolved",
        "sandbox_session_created",
        "sandbox_exec_started",
        "sandbox_exec_completed",
        "sandbox_exec_failed",
        "policy_violation",
        "secret_access_denied",
        "secret_access_brokered",
        "network_access_denied",
        "network_access_allowed",
        "filesystem_access_denied",
        "filesystem_access_allowed",
    ):
        event = TelemetryEvent(
            trace_id="trace-governance",
            event_type=event_type,
            actor=TelemetryActor(type="system"),
        )
        assert event.event_type == event_type


def test_default_profiles_build_with_hashes():
    for profile in ("permissive-dev", "interactive-dev", "strict-production"):
        policy = build_default_policy(profile)

        assert policy.profile.value == profile
        assert policy.provenance.policy_hash


def test_default_policy_denies_credential_and_system_prompt_data_flows():
    policy = build_default_policy("interactive-dev")
    evaluator = PolicyEvaluator()

    credential = evaluator.evaluate(
        policy,
        AuthorityRequest(
            capability="workspace.files.write",
            data_classes=["public", "credential"],
        ),
    )
    system_prompt = evaluator.evaluate(
        policy,
        AuthorityRequest(
            capability="telemetry.export",
            data_classes=["system_prompt"],
        ),
    )

    assert credential.effect == PolicyEffect.DENY
    assert system_prompt.effect == PolicyEffect.DENY


def test_allow_data_class_rule_does_not_match_mixed_sensitive_request():
    policy = policy_from_mapping(
        {
            "name": "data-classes",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "rule_id": "allow_public_network",
                        "capability": "network.http.post",
                        "conditions": {"data_classes": ["public"]},
                    }
                ]
            },
        }
    )

    decision = PolicyEvaluator().evaluate(
        policy,
        AuthorityRequest(
            capability="network.http.post",
            data_classes=["public", "credential"],
        ),
    )

    assert decision.effect == PolicyEffect.DENY


def test_rule_conditions_separate_provider_from_execution_surface():
    policy = policy_from_mapping(
        {
            "name": "surface",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "rule_id": "allow_memory_surface",
                        "capability": "memory.write",
                        "conditions": {
                            "provider": "local",
                            "execution_surface": "memory",
                        },
                    }
                ]
            },
        }
    )

    decision = PolicyEvaluator().evaluate(
        policy,
        AuthorityRequest(
            capability="memory.write",
            provider="local",
            execution_surface="memory",
        ),
    )

    assert decision.effect == PolicyEffect.ALLOW
    assert decision.matched_rule_ids == ["allow_memory_surface"]
