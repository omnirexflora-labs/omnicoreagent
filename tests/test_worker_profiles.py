"""Worker profiles: the lead picks a model, effort, tools and narrower rules
for each worker it spawns (engineering/architecture/worker-profiles-plan.md).
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.config import normalize_agent_config
from omnicoreagent.core.worker_profiles import WorkerProfile, worker_profiles_from_value

EXPLORER = {
    "name": "explorer",
    "description": "Reads and searches the workspace.",
    "model_config": {"model": "gpt-5.4-mini"},
    "reasoning_effort": "low",
    "tools": ["read_file", "grep"],
    "max_steps": 20,
}


# P1 — the setting and its checks


def test_profiles_are_read_from_the_agent_config():
    config = normalize_agent_config("lead", {"enable_subagents": True, "worker_profiles": [EXPLORER]})
    (profile,) = worker_profiles_from_value(config["worker_profiles"])
    assert isinstance(profile, WorkerProfile)
    assert profile.name == "explorer" and profile.max_steps == 20
    assert profile.reasoning_effort == "low" and profile.tools == ["read_file", "grep"]


def test_no_profiles_is_the_default():
    assert normalize_agent_config("lead", {})["worker_profiles"] == []


@pytest.mark.parametrize(
    ("profiles", "message"),
    [
        ([{**EXPLORER, "modle": "x"}], "unknown keys: modle"),
        ([EXPLORER, EXPLORER], "more than once"),
        ([{**EXPLORER, "name": "Has Space"}], "name"),
        ([{**EXPLORER, "description": " "}], "description"),
        ([{k: v for k, v in EXPLORER.items() if k != "description"}], "description"),
        ([{**EXPLORER, "max_steps": 0}], "max_steps"),
        ([{**EXPLORER, "max_steps": 51}], "max_steps"),
        ([{**EXPLORER, "reasoning_effort": "huge"}], "reasoning_effort"),
        ([{**EXPLORER, "tools": "read_file"}], "tools"),
        ([{**EXPLORER, "policy": {"allow": [{"capability": "network.http"}]}}], "only narrow"),
        ([{**EXPLORER, "policy": {"deny": [{"capability": "netwrok.http"}]}}], "netwrok"),
        ([{**EXPLORER, "model_config": {"provider": "anthropic"}}], "model"),
    ],
)
def test_a_bad_profile_is_refused_when_the_config_is_built(profiles, message):
    with pytest.raises(ValueError, match=message):
        normalize_agent_config("lead", {"enable_subagents": True, "worker_profiles": profiles})


def test_profiles_need_subagents_on():
    with pytest.raises(ValueError, match="enable_subagents"):
        normalize_agent_config("lead", {"worker_profiles": [EXPLORER]})
