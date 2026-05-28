from __future__ import annotations

from copy import deepcopy
from typing import Any

from omnicoreagent.governance.hashing import attach_policy_hash
from omnicoreagent.governance.errors import PolicyDeniedError
from omnicoreagent.governance.models import (
    PolicyBudget,
    PolicyEffect,
    PolicyEnvelope,
    PolicyProvenance,
    PolicyRule,
    PolicySource,
    governance_id,
)


POLICY_SNAPSHOT_METADATA_KEY = "governance_policy_snapshot"


def policy_snapshot_from_engine(governance_engine: Any) -> dict[str, Any] | None:
    if governance_engine is None:
        return None
    return policy_snapshot_from_policy(governance_engine.policy)


def policy_snapshot_from_policy(policy: PolicyEnvelope) -> dict[str, Any]:
    budget = None
    if policy.budget is not None:
        budget = {
            "max_requests": policy.budget.max_requests,
            "max_cost": policy.budget.max_cost,
            "used_requests": policy.budget.used_requests,
            "used_cost": policy.budget.used_cost,
            "count_failed_attempts": policy.budget.count_failed_attempts,
        }
    return {
        "policy_id": policy.policy_id,
        "policy_hash": policy.provenance.policy_hash,
        "version": policy.version,
        "name": policy.name,
        "mode": policy.mode.value,
        "profile": policy.profile.value if policy.profile is not None else None,
        "budget": budget,
    }


def attach_policy_snapshot(
    metadata: dict[str, Any] | None,
    governance_engine: Any,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    snapshot = policy_snapshot_from_engine(governance_engine)
    if snapshot is not None:
        updated[POLICY_SNAPSHOT_METADATA_KEY] = snapshot
    return updated


def require_current_policy_snapshot(
    metadata: dict[str, Any] | None,
    governance_engine: Any,
    *,
    surface: str,
    required: bool = False,
) -> None:
    snapshot = (metadata or {}).get(POLICY_SNAPSHOT_METADATA_KEY)
    if not snapshot:
        if required:
            raise PolicyDeniedError(
                f"{surface} is missing a required policy snapshot.",
                metadata={"reason_code": "expired_policy"},
            )
        return
    if governance_engine is None:
        raise PolicyDeniedError(
            f"{surface} has a stored policy snapshot but no governance engine is configured.",
            metadata={
                "reason_code": "expired_policy",
                "stored_policy_hash": snapshot.get("policy_hash"),
                "stored_policy_id": snapshot.get("policy_id"),
            },
        )
    current = policy_snapshot_from_engine(governance_engine) or {}
    if snapshot.get("version") != current.get("version"):
        raise PolicyDeniedError(
            f"{surface} was created under a different policy schema version.",
            metadata={
                "reason_code": "expired_policy",
                "stored_policy_version": snapshot.get("version"),
                "current_policy_version": current.get("version"),
                "stored_policy_hash": snapshot.get("policy_hash"),
                "current_policy_hash": current.get("policy_hash"),
            },
        )
    if snapshot.get("policy_hash") != current.get("policy_hash"):
        raise PolicyDeniedError(
            f"{surface} was created under a different policy snapshot.",
            metadata={
                "reason_code": "expired_policy",
                "stored_policy_hash": snapshot.get("policy_hash"),
                "current_policy_hash": current.get("policy_hash"),
                "stored_policy_id": snapshot.get("policy_id"),
                "current_policy_id": current.get("policy_id"),
            },
        )
    _restore_budget_floor(snapshot, governance_engine)


def derive_subagent_policy(
    parent_policy: PolicyEnvelope,
    *,
    subagent_name: str,
) -> PolicyEnvelope:
    child = deepcopy(parent_policy)
    child.policy_id = governance_id("policy")
    child.policy_id_supplied = True
    child.name = f"{parent_policy.name}:subagent:{subagent_name}"
    child.provenance = PolicyProvenance(
        source=PolicySource.INHERITED,
        parent_policy_id=parent_policy.policy_id,
    )
    child.rules.deny = [
        PolicyRule(
            rule_id="deny_recursive_subagent_spawn",
            effect=PolicyEffect.DENY,
            capability="subagent.spawn",
            reason="Subagents cannot spawn nested subagents unless a later scoped policy explicitly allows it.",
        ),
        *child.rules.deny,
    ]
    child.metadata = {
        **child.metadata,
        "derived_for": "subagent",
        "subagent_name": subagent_name,
        "parent_policy_hash": parent_policy.provenance.policy_hash,
    }
    if parent_policy.budget is not None:
        child.budget = PolicyBudget(
            max_requests=0 if parent_policy.budget.max_requests is not None else None,
            max_cost=0.0 if parent_policy.budget.max_cost is not None else None,
            used_requests=0,
            used_cost=0.0,
            count_failed_attempts=parent_policy.budget.count_failed_attempts,
        )
        child.metadata["budget_narrowing"] = (
            "Subagent child policies receive zero budget until explicit budget "
            "allocation is implemented."
        )
    return attach_policy_hash(child)


def _restore_budget_floor(snapshot: dict[str, Any], governance_engine: Any) -> None:
    stored_budget = snapshot.get("budget") or {}
    current_budget = getattr(governance_engine.policy, "budget", None)
    if current_budget is None or not stored_budget:
        return
    current_budget.used_requests = max(
        current_budget.used_requests,
        int(stored_budget.get("used_requests") or 0),
    )
    current_budget.used_cost = max(
        current_budget.used_cost,
        float(stored_budget.get("used_cost") or 0.0),
    )
