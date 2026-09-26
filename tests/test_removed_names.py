"""A name removed in 0.4 says what replaced it (stranger test, round two).

`from omnicoreagent import SequentialAgent` raised a bare "cannot import
name", while a removed agent_config key names its replacement.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("SequentialAgent", "sub_agents"),
        ("ParallelAgent", "asyncio.gather"),
        ("RouterAgent", "sub_agents"),
        ("DeepAgent", "enable_subagents"),
        ("OmniAgent", "OmniCoreAgent"),
        ("EventRouter", "telemetry"),
        ("BackgroundOmniCoreAgent", "BackgroundAgentManager"),
    ],
)
def test_a_removed_name_points_to_its_replacement(name, hint):
    with pytest.raises(ImportError) as error:
        exec(f"from omnicoreagent import {name}")
    message = str(error.value)
    assert hint in message and "/docs/upgrading" in message


def test_an_unknown_name_is_still_an_attribute_error():
    import omnicoreagent

    with pytest.raises(AttributeError):
        omnicoreagent.NoSuchThing  # noqa: B018


def test_a_removed_name_is_still_absent_to_hasattr_and_getattr():
    import omnicoreagent

    assert not hasattr(omnicoreagent, "DeepAgent")
    assert getattr(omnicoreagent, "SequentialAgent", None) is None
