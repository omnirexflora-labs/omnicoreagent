from __future__ import annotations

import hashlib
import json
from typing import Any

from omnicoreagent.governance.models import PolicyEnvelope, to_plain


def canonical_policy_payload(policy: PolicyEnvelope) -> dict[str, Any]:
    payload = to_plain(policy)
    payload.pop("policy_id_supplied", None)
    if not policy.policy_id_supplied:
        payload.pop("policy_id", None)
    provenance = payload.get("provenance") or {}
    for key in ("loaded_at", "policy_hash"):
        provenance.pop(key, None)
    if not provenance.get("source_ref"):
        provenance.pop("source_ref", None)
    if not provenance.get("created_by"):
        provenance.pop("created_by", None)
    if not provenance.get("parent_policy_id"):
        provenance.pop("parent_policy_id", None)
    payload["provenance"] = provenance
    return _normalize(payload)


def policy_hash(policy: PolicyEnvelope) -> str:
    canonical = json.dumps(
        canonical_policy_payload(policy),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attach_policy_hash(policy: PolicyEnvelope) -> PolicyEnvelope:
    policy.provenance.policy_hash = policy_hash(policy)
    return policy


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            item = _normalize(item)
            if item is None:
                continue
            if item == [] or item == {}:
                continue
            normalized[key] = item
        return normalized
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value
