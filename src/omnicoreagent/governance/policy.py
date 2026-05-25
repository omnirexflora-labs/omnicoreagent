from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from omnicoreagent.governance.defaults import build_default_policy
from omnicoreagent.governance.errors import PolicyLoadError
from omnicoreagent.governance.hashing import attach_policy_hash
from omnicoreagent.governance.models import (
    PolicyEnvelope,
    PolicyProfile,
    PolicyRule,
    PolicyRuleSet,
    PolicySource,
)

DEFAULT_POLICY_FILENAMES = (
    "omnicoreagent.policy.json",
    ".omnicoreagent/policy.json",
)

AGENT_WRITABLE_PARTS = frozenset(
    {
        "workspace",
        "workspaces",
        "artifact",
        "artifacts",
        "output",
        "outputs",
        "tmp",
        "temp",
    }
)


def load_policy(
    *,
    policy: PolicyEnvelope | dict[str, Any] | None = None,
    explicit_path: str | Path | None = None,
    project_root: str | Path | None = None,
    profile: PolicyProfile | str = PolicyProfile.INTERACTIVE_DEV,
) -> PolicyEnvelope:
    root = Path(project_root or Path.cwd()).resolve()
    if explicit_path is not None:
        return load_policy_file(explicit_path, project_root=root, explicit=True)
    if policy is not None:
        if isinstance(policy, PolicyEnvelope):
            policy.provenance.source = PolicySource.CODE
            return attach_policy_hash(policy)
        return policy_from_mapping(policy, source=PolicySource.CODE)
    discovered = discover_policy_file(root)
    if discovered is not None:
        discovered_policy = load_policy_file(discovered, project_root=root, explicit=False)
        baseline = build_default_policy(profile)
        return _compose_auto_discovered_policy(discovered_policy, baseline)
    return build_default_policy(profile)


def discover_policy_file(project_root: str | Path) -> Path | None:
    root = Path(project_root).resolve()
    for name in DEFAULT_POLICY_FILENAMES:
        path = root / name
        if path.exists():
            return _validate_policy_path(path, root, explicit=False)
    return None


def load_policy_file(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
    explicit: bool = True,
) -> PolicyEnvelope:
    root = Path(project_root or Path(path).parent).resolve()
    safe_path = _validate_policy_path(Path(path), root, explicit=explicit)
    data = _read_policy_file(safe_path)
    try:
        envelope = policy_from_mapping(
            data,
            source=PolicySource.FILE,
            source_ref=str(safe_path),
        )
    except Exception as exc:
        raise PolicyLoadError(f"Invalid policy file: {safe_path}") from exc
    if not explicit:
        envelope.metadata["auto_discovered"] = True
        envelope.metadata["may_only_narrow_trusted_baseline"] = True
    return envelope


def policy_from_mapping(
    data: dict[str, Any],
    *,
    source: PolicySource | str = PolicySource.CODE,
    source_ref: str | None = None,
) -> PolicyEnvelope:
    payload = dict(data)
    supplied_policy_id = "policy_id" in payload
    payload["policy_id_supplied"] = supplied_policy_id
    payload["rules"] = _normalize_rules(payload.get("rules") or {})
    provenance = dict(payload.get("provenance") or {})
    provenance.setdefault("source", PolicySource(source).value)
    provenance.setdefault("source_ref", source_ref)
    payload["provenance"] = provenance
    envelope = PolicyEnvelope(**payload)
    return attach_policy_hash(envelope)


def _normalize_rules(data: dict[str, Any]) -> PolicyRuleSet:
    return PolicyRuleSet(
        deny=[_normalize_rule(item, "deny") for item in data.get("deny", [])],
        ask=[_normalize_rule(item, "ask") for item in data.get("ask", [])],
        allow=[_normalize_rule(item, "allow") for item in data.get("allow", [])],
    )


def _normalize_rule(data: dict[str, Any], effect: str) -> PolicyRule:
    payload = dict(data)
    payload.setdefault("effect", effect)
    return PolicyRule(**payload)


def _validate_policy_path(path: Path, project_root: Path, *, explicit: bool) -> Path:
    if not explicit and path.is_symlink():
        raise PolicyLoadError(f"Auto-discovered policy file must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PolicyLoadError(f"Policy file not found: {path}") from exc
    root = project_root.resolve()
    if not _is_relative_to(resolved, root):
        raise PolicyLoadError(f"Policy file escapes trusted project root: {resolved}")
    relative_parts = resolved.relative_to(root).parts[:-1]
    if any(part in AGENT_WRITABLE_PARTS for part in relative_parts):
        raise PolicyLoadError(f"Policy file is inside an agent-writable directory: {resolved}")
    return resolved


def _read_policy_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyLoadError(f"Could not read policy file: {path}") from exc
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyLoadError(f"Invalid JSON policy file: {path}") from exc
    elif suffix in {".yaml", ".yml"}:
        raise PolicyLoadError(
            "YAML policy loading is not enabled in Phase 1. Use JSON or in-code policy."
        )
    else:
        raise PolicyLoadError(f"Unsupported policy file extension: {path.suffix}")
    if not isinstance(data, dict):
        raise PolicyLoadError("Policy file must contain an object at the top level.")
    return data


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _compose_auto_discovered_policy(
    discovered: PolicyEnvelope,
    baseline: PolicyEnvelope,
) -> PolicyEnvelope:
    _validate_auto_discovered_allow_rules(discovered, baseline)
    discovered.rules.deny = [*baseline.rules.deny, *discovered.rules.deny]
    discovered.rules.ask = [*baseline.rules.ask, *discovered.rules.ask]
    discovered.mode = _stricter_mode(baseline.mode, discovered.mode)
    discovered.profile = baseline.profile
    return attach_policy_hash(discovered)


def _validate_auto_discovered_allow_rules(
    discovered: PolicyEnvelope,
    baseline: PolicyEnvelope,
) -> None:
    for rule in discovered.rules.allow:
        if any(
            fnmatchcase(rule.capability, baseline_rule.capability)
            for baseline_rule in baseline.rules.allow
        ):
            continue
        raise PolicyLoadError(
            "Auto-discovered policy allow rule broadens the default baseline: "
            f"{rule.rule_id}"
        )


def _stricter_mode(left, right):
    order = {"permissive": 0, "interactive": 1, "strict": 2}
    return left if order[left.value] >= order[right.value] else right
