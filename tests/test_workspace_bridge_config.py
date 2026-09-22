"""The workspace bridge's patterns are configured with the sandbox:
``governance_config.workspace_bridge = {"include": [...], "exclude": [...]}``."""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

_MODEL = {"provider": "openai", "model": "gpt-4o", "api_key": "test"}


def _agent(bridge):
    return OmniCoreAgent(
        name="builder",
        system_instruction="Use execute.",
        model_config=_MODEL,
        agent_config={"governance_config": {"enabled": True, "workspace_bridge": bridge}},
    )


def test_bridge_patterns_are_accepted():
    _agent({"include": ["src/*"], "exclude": ["*.log"]})


@pytest.mark.parametrize("bridge", [{"include": "src/*"}, {"only": ["x"]}, ["src/*"], {"exclude": [1]}])
def test_bridge_patterns_that_cannot_be_read_are_refused_at_startup(bridge):
    with pytest.raises(ValueError, match="workspace_bridge"):
        _agent(bridge)
