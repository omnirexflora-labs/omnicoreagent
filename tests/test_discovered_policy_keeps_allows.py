"""R3 (0.5.0rc1 gate): a policy file found in the project narrows the profile.

The docs say an auto-discovered policy file "can only narrow". Its deny and
ask rules were added to the profile's, but its allow list replaced the
profile's: a file with one ask rule took every allow away. Under
interactive-dev, local tools, workspace files and sandboxed commands all
became asks; under permissive-dev everything was still allowed, but by the
mode, so the evidence named no rule. The profile's allow rules stay.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.governance.evaluator import PolicyEvaluator
from omnicoreagent.governance.models import AuthorityRequest
from omnicoreagent.governance.policy import DEFAULT_POLICY_FILENAMES, load_policy

FILE = {"name": "project", "rules": {"ask": [
    {"rule_id": "ask_before_refunds", "capability": "tool.local.call", "target": {"tool_name": "issue_refund"}}
]}}


@pytest.mark.parametrize("profile", ["permissive-dev", "interactive-dev"])
def test_a_discovered_file_keeps_the_profiles_allow_rules(tmp_path, profile):
    (tmp_path / DEFAULT_POLICY_FILENAMES[0]).write_text(json.dumps(FILE))

    policy = load_policy(project_root=tmp_path, profile=profile)

    def decide(capability, **target):
        return PolicyEvaluator().evaluate(policy, AuthorityRequest(capability=capability, target=target or None))

    lookup = decide("tool.local.call", tool_name="lookup_order")
    assert lookup.effect.value == "allow" and lookup.matched_rule_ids == ["allow_local_tools"]
    refund = decide("tool.local.call", tool_name="issue_refund")
    assert refund.effect.value == "ask" and refund.matched_rule_ids == ["ask_before_refunds"]
