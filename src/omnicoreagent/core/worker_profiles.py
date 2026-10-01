"""Worker profiles: the kinds of worker a lead may spawn.

A developer lists them in ``agent_config["worker_profiles"]``; the lead picks
one for each worker it starts with ``spawn_subagents``, by its name and
description. A profile sets the worker's model and reasoning effort, narrows
its tools, MCP servers and steps, and may add deny and ask rules; it never
gives a worker more than its lead (engineering/architecture/
worker-profiles-plan.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any

# The levels providers take; anything else would only fail at the first call.
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
MAX_WORKER_STEPS = 50
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class WorkerProfile:
    # What the lead names in spawn_subagents: lowercase letters, digits, _ and -.
    name: str
    # What the lead reads to choose this kind of worker.
    description: str
    # Laid over the lead's model config: with the same provider (or none
    # given) unset fields, the key among them, come from the lead's; with
    # another provider it is used whole, and the lead's key is not sent.
    model_config: dict[str, Any] | None = None
    # Shortcut for model_config["reasoning_effort"].
    reasoning_effort: str | None = None
    # Local tools the worker gets, by name; None is all of the lead's.
    # write_file is always kept: a worker writes its output to a file.
    tools: list[str] | None = None
    # MCP servers the worker gets, by name; None is all of the lead's.
    mcp_servers: list[str] | None = None
    # Deny and ask rules added to the policy the worker inherits. No allow:
    # a worker never gets more than its lead.
    policy: dict[str, Any] | None = None
    # 1 to 50, and never more than the lead's max_steps. None: the smaller
    # of the lead's and 50.
    max_steps: int | None = None
    # Added to the worker's system prompt, after its role and task.
    instructions: str | None = None

    def model_config_over(self, lead: dict[str, Any]) -> dict[str, Any]:
        """The worker's model config, laid over the lead's."""
        own = dict(self.model_config or {})
        if own.get("provider") and own["provider"] != lead.get("provider"):
            config = own
        else:
            config = {**lead, **own}
        if self.reasoning_effort is not None:
            config["reasoning_effort"] = self.reasoning_effort
        return config

    def steps_under(self, lead_max_steps: int) -> int:
        cap = min(lead_max_steps, MAX_WORKER_STEPS)
        return cap if self.max_steps is None else min(self.max_steps, cap)


_KEYS = frozenset(item.name for item in fields(WorkerProfile))


def worker_profiles_from_value(value: Any) -> list[WorkerProfile]:
    """The profiles in an agent config, checked; a bad one is refused with
    what is wrong, when the config is built rather than at the first spawn."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("worker_profiles must be a list of profiles")
    profiles: list[WorkerProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, WorkerProfile):
            item = {f.name: getattr(item, f.name) for f in fields(WorkerProfile)}
        if not isinstance(item, dict):
            raise ValueError(f"worker_profiles[{index}] must be a dict")
        where = f"worker_profiles[{index}]"
        unknown = set(item) - _KEYS
        if unknown:
            raise ValueError(
                f"{where} has unknown keys: {', '.join(sorted(unknown))}. "
                f"Allowed keys: {', '.join(sorted(_KEYS))}"
            )
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(
                f"{where}.name must be lowercase letters, digits, _ or - "
                f"(starting with a letter, at most 40), got {name!r}"
            )
        if name in seen:
            raise ValueError(f"worker profile {name!r} is named more than once")
        seen.add(name)
        where = f"worker profile {name!r}"
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{where} needs a description: the lead chooses by it")
        _check_model_config(where, item.get("model_config"))
        effort = item.get("reasoning_effort")
        if effort is not None and effort not in REASONING_EFFORTS:
            raise ValueError(
                f"{where}: reasoning_effort must be one of "
                f"{', '.join(sorted(REASONING_EFFORTS))}, got {effort!r}"
            )
        for key in ("tools", "mcp_servers"):
            names = item.get(key)
            if names is not None and (
                not isinstance(names, list) or not all(isinstance(n, str) and n for n in names)
            ):
                raise ValueError(f"{where}: {key} must be a list of names, or None for all of the lead's")
        steps = item.get("max_steps")
        if steps is not None and (
            not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= MAX_WORKER_STEPS
        ):
            raise ValueError(f"{where}: max_steps must be between 1 and {MAX_WORKER_STEPS}, got {steps!r}")
        _check_policy(where, item.get("policy"))
        instructions = item.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            raise ValueError(f"{where}: instructions must be text")
        profiles.append(WorkerProfile(**item))
    return profiles


def _check_model_config(where: str, value: Any) -> None:
    if value is None:
        return
    from omnicoreagent.core.runtime.config import ModelConfig

    if not isinstance(value, dict):
        raise ValueError(f"{where}: model_config must be a dict")
    allowed = {item.name for item in fields(ModelConfig)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{where}: model_config has unknown keys: {', '.join(sorted(unknown))}")
    if value.get("provider") and not value.get("model"):
        raise ValueError(
            f"{where}: model_config names a provider without a model; "
            "a different provider is used whole"
        )


def _check_policy(where: str, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{where}: policy must be a dict of deny and ask rules")
    if "allow" in value:
        raise ValueError(
            f"{where}: a profile's policy can only narrow the lead's: "
            "deny and ask rules, no allow"
        )
    unknown = set(value) - {"deny", "ask"}
    if unknown:
        raise ValueError(f"{where}: policy takes deny and ask rules, not {', '.join(sorted(unknown))}")
    from omnicoreagent.governance.policy import policy_from_mapping

    try:
        policy_from_mapping({"name": "profile", "mode": "strict", "rules": _with_ids("profile", value)})
    except Exception as exc:  # the policy loader says what is wrong
        raise ValueError(f"{where}: {exc}") from exc


def profile_rules(profile: WorkerProfile) -> dict[str, list]:
    """The profile's deny and ask rules, as PolicyRule objects."""
    if not profile.policy:
        return {"deny": [], "ask": []}
    from omnicoreagent.governance.policy import policy_from_mapping

    loaded = policy_from_mapping(
        {"name": f"profile-{profile.name}", "mode": "strict", "rules": _with_ids(profile.name, profile.policy)}
    )
    return {"deny": list(loaded.rules.deny), "ask": list(loaded.rules.ask)}


def _with_ids(profile_name: str, rules: dict[str, Any]) -> dict[str, list]:
    """The rules, each with a rule_id: a profile's rule names its profile, so
    the evidence of a refusal says which profile's rule it was."""
    out: dict[str, list] = {}
    for effect, items in rules.items():
        out[effect] = [
            {"rule_id": f"{profile_name}_{effect}_{i}", **item} if isinstance(item, dict) else item
            for i, item in enumerate(items or [], start=1)
        ]
    return out
