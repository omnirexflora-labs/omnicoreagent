"""An agent_config written for 0.3.8 either works or says what to change.

Measured on 2026-09-25 against 0.3.8 (the last 0.3 release that runs: 0.3.9
was published without one of its own modules). Every 0.3.8 key but one is
still accepted; that one, `memory_tool_backend`, failed as a bare TypeError,
and 0.3.8's own default for `guardrail_config`, None, failed validation.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def agent(**agent_config):
    return OmniCoreAgent(
        name="a", system_instruction="x", model_config=MODEL, agent_config=agent_config
    )


def test_a_guardrail_config_of_none_means_none_as_it_did_in_0_3():
    assert agent(guardrail_config=None).agent_config["guardrail_config"] == {}


def test_the_removed_memory_tool_says_what_replaced_it():
    with pytest.raises(ValueError) as error:
        agent(memory_tool_backend="local")
    message = str(error.value)
    assert "memory_tool_backend" in message
    assert "workspace" in message
    assert "/docs/upgrading" in message


def test_an_unknown_key_is_named_with_the_keys_that_exist():
    with pytest.raises(ValueError) as error:
        agent(max_step=10)
    message = str(error.value)
    assert "max_step" in message and "max_steps" in message


def test_sub_agents_given_as_a_dict_is_refused_when_the_agent_is_built():
    """0.3's signature said Dict; its code, and 0.4's, iterate a list of agents.
    A dict reached the run as its keys, strings with no `run`."""
    child = OmniCoreAgent(name="child", system_instruction="x", model_config=MODEL)
    with pytest.raises(ValueError, match="sub_agents must be a list"):
        OmniCoreAgent(
            name="a", system_instruction="x", model_config=MODEL, sub_agents={"child": child}
        )
    parent = OmniCoreAgent(
        name="a", system_instruction="x", model_config=MODEL, sub_agents=[child]
    )
    assert parent.sub_agents == [child]
