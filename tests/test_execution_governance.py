"""Governance for code execution: default profiles, capabilities, conditions.

Decisions (2026-09-19): permissive-dev and interactive-dev allow sandboxed
execution with the network off and ask when a manifest turns the network on;
strict-production needs explicit rules; host process execution is never allowed
by default; skill scripts on the host are their own capability.
"""

from __future__ import annotations

import pytest

from omnicoreagent.governance import (
    AuthorityRequest,
    GovernanceEngine,
    PolicyEffect,
    PolicyRule,
    PolicyRuleConditions,
    build_default_policy,
)
from omnicoreagent.governance.capabilities import tool_capability_name
from omnicoreagent.governance.evaluator import PolicyEvaluator
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    NetworkPolicy,
    SandboxCommandSpec,
    SandboxExecResult,
    SandboxExecutionService,
    SandboxManifest,
)


def _effect(profile: str, **request) -> str:
    decision = PolicyEvaluator().evaluate(build_default_policy(profile), AuthorityRequest(**request))
    return decision.effect.value


SANDBOXED = {"capability": "process.exec", "provider": "sandbox", "execution_surface": "sandbox", "risk_level": "high"}
HOST = {"capability": "process.exec", "provider": "local", "execution_surface": "host", "risk_level": "high"}
NETWORK_ON = {"capability": "sandbox.network.configure", "execution_surface": "sandbox", "risk_level": "medium"}
SKILL_HOST = {"capability": "skill.script.run", "provider": "skill", "execution_surface": "host", "risk_level": "high"}
EXECUTE_TOOL = {"capability": "sandbox.execute", "provider": "sandbox", "execution_surface": "sandbox", "risk_level": "high"}
IMAGE = {"capability": "sandbox.image.use", "execution_surface": "sandbox"}


@pytest.mark.parametrize(
    ("profile", "request_", "expected"),
    [
        ("permissive-dev", SANDBOXED, "allow"),
        ("permissive-dev", HOST, "deny"),
        ("permissive-dev", NETWORK_ON, "ask"),
        ("permissive-dev", SKILL_HOST, "allow"),
        ("permissive-dev", EXECUTE_TOOL, "allow"),
        ("permissive-dev", IMAGE, "allow"),
        ("interactive-dev", SANDBOXED, "allow"),
        ("interactive-dev", HOST, "ask"),
        ("interactive-dev", NETWORK_ON, "ask"),
        ("interactive-dev", SKILL_HOST, "allow"),
        ("interactive-dev", EXECUTE_TOOL, "allow"),
        ("interactive-dev", IMAGE, "allow"),
        ("strict-production", SANDBOXED, "deny"),
        ("strict-production", HOST, "deny"),
        ("strict-production", SKILL_HOST, "deny"),
        ("strict-production", EXECUTE_TOOL, "deny"),
    ],
)
def test_default_profiles_decide_execution_as_agreed(profile, request_, expected):
    assert _effect(profile, **request_) == expected


def test_a_rule_can_exclude_an_execution_surface():
    rule = PolicyRule(
        rule_id="ask_non_sandboxed",
        effect=PolicyEffect.ASK,
        capability="process.*",
        conditions=PolicyRuleConditions(exclude_execution_surface=["sandbox"]),
    )
    from omnicoreagent.governance.evaluator import _rule_matches

    assert _rule_matches(rule, AuthorityRequest(**HOST)) is True
    assert _rule_matches(rule, AuthorityRequest(**SANDBOXED)) is False


def test_skill_and_sandbox_tools_have_their_own_capabilities():
    assert tool_capability_name(tool_name="run_skill_script", tool_provider="skill") == "skill.script.run"
    assert tool_capability_name(tool_name="read_skill_file", tool_provider="skill") == "skill.files.read"
    assert tool_capability_name(tool_name="execute", tool_provider="sandbox") == "sandbox.execute"


def test_skill_tools_are_marked_as_skill_provider_tools():
    from omnicoreagent.core.skills.tools import build_skill_tools
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    registry = build_skill_tools(skill_manager=object(), registry=ToolRegistry())

    assert registry.get_tool_provider("run_skill_script") == "skill"
    assert registry.get_tool_provider("read_skill_file") == "skill"


def _engine(profile="interactive-dev"):
    runtime = LocalTestSandboxRuntime(
        commands={"echo": lambda request: SandboxExecResult(exit_code=0, stdout=" ".join(request.command[1:]))}
    )
    return GovernanceEngine(
        build_default_policy(profile), sandbox_runtime=runtime, allow_test_sandbox_runtime=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["permissive-dev", "interactive-dev"])
async def test_the_default_profiles_run_contained_commands_without_a_written_policy(profile):
    result = await SandboxExecutionService(_engine(profile)).execute(
        SandboxCommandSpec(command=["echo", "contained"])
    )

    assert result.stdout == "contained"
    assert result.metadata["authority"]["matched_rule_ids"] == ["allow_sandboxed_execution"]


@pytest.mark.asyncio
async def test_turning_the_network_on_needs_approval_by_default():
    from omnicoreagent.governance.errors import GovernanceError

    manifest = SandboxManifest(network_policy=NetworkPolicy(default="allow"))
    with pytest.raises(GovernanceError):
        await SandboxExecutionService(_engine()).open_session(manifest)


@pytest.mark.asyncio
async def test_strict_production_needs_an_explicit_rule_to_execute():
    from omnicoreagent.governance.errors import GovernanceError

    with pytest.raises(GovernanceError):
        await SandboxExecutionService(_engine("strict-production")).execute(
            SandboxCommandSpec(command=["echo", "x"])
        )
