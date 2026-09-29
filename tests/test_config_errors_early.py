"""R14 (0.5.0rc1 gate): a bad policy or sandbox setting is refused when the agent
is built, and says why.

The gate found: a bad policy file said only "Invalid policy file" (the reason
was in __cause__); a bad dict policy and unknown sandbox options were refused
only at the first run(), though the configuration page says settings are
checked when the agent is built; a rule error repeated its rule id ("rule r1:
rule r1 does not match..."); an unknown `command` key surfaced as a raw
TypeError from CommandMatcher.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.governance.errors import PolicyLoadError
from omnicoreagent.governance.policy import load_policy_file, policy_from_mapping
from test_execute_tool import _MODEL


def _build(governance):
    return OmniCoreAgent(name="a", system_instruction="Help.", model_config=_MODEL,
                         agent_config={"governance_config": governance})


def test_a_bad_dict_policy_is_refused_when_the_agent_is_built():
    with pytest.raises(ValueError, match="rule r1: unknown key"):
        _build({"policy": {"name": "p", "rules": {"deny": [{"rule_id": "r1", "capability": "x", "targte": {}}]}}})


def test_unknown_sandbox_options_are_refused_when_the_agent_is_built():
    with pytest.raises(ValueError, match="Unknown docker sandbox option"):
        _build({"sandbox_config": {"provider": "docker", "options": {"network": "bridge"}}})


def test_a_bad_policy_file_says_why(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"name": "p", "rules": {"deny": [{"rule_id": "r1", "capability": "x", "targte": {}}]}}))
    with pytest.raises(PolicyLoadError, match="Invalid policy file: .*rule r1: unknown key"):
        load_policy_file(path)


def test_a_rule_error_names_the_rule_once():
    with pytest.raises(PolicyLoadError) as refused:
        policy_from_mapping({"name": "p", "rules": {"deny": [{
            "rule_id": "r1", "capability": "process.exec", "command": {"program": "rm"},
            "examples": {"not_match": ["rm -rf x"]}}]}})
    assert str(refused.value).count("r1") == 1, str(refused.value)


def test_an_unknown_command_key_is_a_clear_error():
    with pytest.raises(PolicyLoadError, match="rule r1: command has unknown key.*programme"):
        policy_from_mapping({"name": "p", "rules": {"deny": [{
            "rule_id": "r1", "capability": "process.exec", "command": {"programme": "rm"}}]}})


@pytest.mark.parametrize("part", ["target", "conditions", "constraints"])
def test_an_unknown_key_in_any_part_of_a_rule_is_named(part):
    # The 0.5.0rc2 gate: `command` said "command has unknown key(s) ..." but a
    # misspelt target key surfaced as a raw TargetMatcher.__init__ TypeError.
    with pytest.raises(PolicyLoadError) as refused:
        policy_from_mapping({"name": "p", "rules": {"deny": [{
            "rule_id": "r1", "capability": "tool.local.call", part: {"tool": "x"}}]}})
    assert str(refused.value) == f"rule r1: {part} has unknown key(s) tool"


def test_a_malformed_json_policy_file_says_where(tmp_path):
    # The 0.5.0rc2 gate: the line and column were only in __cause__.
    path = tmp_path / "policy.json"
    path.write_text('{"name": "p",\n "rules": {"deny": [}\n}')
    with pytest.raises(PolicyLoadError, match=r"Invalid JSON policy file: .*policy.json: .*line 2 column"):
        load_policy_file(path)
