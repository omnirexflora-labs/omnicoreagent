from __future__ import annotations

from omnicoreagent.governance.hashing import attach_policy_hash
from omnicoreagent.governance.models import (
    PolicyConstraints,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyProfile,
    PolicyProvenance,
    PolicyRule,
    PolicyRuleConditions,
    PolicyRuleSet,
    PolicySource,
)


def build_default_policy(
    profile: PolicyProfile | str = PolicyProfile.INTERACTIVE_DEV,
) -> PolicyEnvelope:
    selected = PolicyProfile(profile)
    if selected == PolicyProfile.PERMISSIVE_DEV:
        policy = _permissive_dev_policy()
    elif selected == PolicyProfile.INTERACTIVE_DEV:
        policy = _interactive_dev_policy()
    elif selected == PolicyProfile.STRICT_PRODUCTION:
        policy = _strict_production_policy()
    else:  # pragma: no cover - enum construction guards this.
        raise ValueError(f"Unknown policy profile: {profile}")
    return attach_policy_hash(policy)


def _sandbox_ask_rules() -> list[PolicyRule]:
    """Asks shared by the dev profiles: leaving the sandbox's containment."""
    return [
        PolicyRule(
            rule_id="ask_sandbox_network",
            effect=PolicyEffect.ASK,
            capability="sandbox.network.configure",
            reason="Turning the sandbox network on needs approval.",
        ),
        PolicyRule(
            rule_id="ask_sandbox_host_mount",
            effect=PolicyEffect.ASK,
            capability="sandbox.filesystem.mount",
            reason="Mounting host files into the sandbox needs approval.",
        ),
    ]


def _sandbox_allow_rules() -> list[PolicyRule]:
    """Contained execution and skill scripts, allowed by the dev profiles."""
    return [
        PolicyRule(
            rule_id="allow_sandboxed_execution",
            effect=PolicyEffect.ALLOW,
            capability="process.exec",
            conditions=PolicyRuleConditions(execution_surface="sandbox"),
            constraints=PolicyConstraints(sandbox_required=True),
        ),
        PolicyRule(
            rule_id="allow_sandbox_setup",
            effect=PolicyEffect.ALLOW,
            capability="sandbox.*",
        ),
        PolicyRule(
            rule_id="allow_skill_scripts",
            effect=PolicyEffect.ALLOW,
            capability="skill.*",
        ),
    ]


def _permissive_dev_policy() -> PolicyEnvelope:
    return PolicyEnvelope(
        name="permissive-dev",
        mode=PolicyMode.PERMISSIVE,
        profile=PolicyProfile.PERMISSIVE_DEV,
        provenance=PolicyProvenance(source=PolicySource.DEFAULT),
        rules=PolicyRuleSet(
            deny=[
                PolicyRule(
                    rule_id="deny_credential_or_system_prompt_flow",
                    effect=PolicyEffect.DENY,
                    capability="*",
                    conditions=PolicyRuleConditions(
                        data_classes=["credential", "system_prompt"]
                    ),
                    reason="Credential and system prompt data require a brokered boundary.",
                ),
                PolicyRule(
                    rule_id="deny_raw_secret_read",
                    effect=PolicyEffect.DENY,
                    capability="secret.read",
                ),
                PolicyRule(
                    rule_id="deny_unconfigured_secret_broker_use",
                    effect=PolicyEffect.DENY,
                    capability="secret.use",
                    reason="Brokered secret use needs explicit policy.",
                ),
                PolicyRule(
                    rule_id="deny_unrestricted_process_exec",
                    effect=PolicyEffect.DENY,
                    capability="process.exec",
                    conditions=PolicyRuleConditions(exclude_execution_surface=["sandbox"]),
                    reason="Host process execution needs explicit policy.",
                ),
                PolicyRule(
                    rule_id="deny_unrestricted_host_filesystem_access",
                    effect=PolicyEffect.DENY,
                    capability="filesystem.*",
                    reason="Host filesystem access needs explicit policy.",
                ),
                PolicyRule(
                    rule_id="deny_unrestricted_network_egress",
                    effect=PolicyEffect.DENY,
                    capability="network.*",
                    reason="Network egress needs explicit policy.",
                ),
                PolicyRule(
                    rule_id="deny_unrestricted_package_install",
                    effect=PolicyEffect.DENY,
                    capability="package.install",
                    reason="Package installation needs explicit policy.",
                ),
            ],
            ask=_sandbox_ask_rules(),
            allow=[
                *_sandbox_allow_rules(),
                PolicyRule(
                    rule_id="allow_local_dev_workspace",
                    effect=PolicyEffect.ALLOW,
                    capability="workspace.*",
                ),
                PolicyRule(
                    rule_id="allow_local_tools",
                    effect=PolicyEffect.ALLOW,
                    capability="tool.local.call",
                ),
                PolicyRule(
                    rule_id="allow_memory_and_telemetry",
                    effect=PolicyEffect.ALLOW,
                    capability="memory.*",
                ),
                PolicyRule(
                    rule_id="allow_telemetry",
                    effect=PolicyEffect.ALLOW,
                    capability="telemetry.*",
                ),
                PolicyRule(
                    rule_id="allow_subagent_spawn",
                    effect=PolicyEffect.ALLOW,
                    capability="subagent.*",
                ),
                PolicyRule(
                    rule_id="allow_background_execution",
                    effect=PolicyEffect.ALLOW,
                    capability="background.*",
                ),
            ],
        ),
    )


def _interactive_dev_policy() -> PolicyEnvelope:
    return PolicyEnvelope(
        name="interactive-dev",
        mode=PolicyMode.INTERACTIVE,
        profile=PolicyProfile.INTERACTIVE_DEV,
        provenance=PolicyProvenance(source=PolicySource.DEFAULT),
        rules=PolicyRuleSet(
            deny=[
                PolicyRule(
                    rule_id="deny_credential_or_system_prompt_flow",
                    effect=PolicyEffect.DENY,
                    capability="*",
                    conditions=PolicyRuleConditions(
                        data_classes=["credential", "system_prompt"]
                    ),
                    reason="Credential and system prompt data require a brokered boundary.",
                ),
                PolicyRule(
                    rule_id="deny_raw_secret_read",
                    effect=PolicyEffect.DENY,
                    capability="secret.read",
                ),
            ],
            ask=[
                PolicyRule(
                    rule_id="ask_process_exec",
                    effect=PolicyEffect.ASK,
                    capability="process.*",
                    conditions=PolicyRuleConditions(exclude_execution_surface=["sandbox"]),
                ),
                *_sandbox_ask_rules(),
                PolicyRule(
                    rule_id="ask_network_egress",
                    effect=PolicyEffect.ASK,
                    capability="network.*",
                ),
                PolicyRule(
                    rule_id="ask_package_install",
                    effect=PolicyEffect.ASK,
                    capability="package.install",
                ),
                PolicyRule(
                    rule_id="ask_secret_broker_use",
                    effect=PolicyEffect.ASK,
                    capability="secret.use",
                ),
                PolicyRule(
                    rule_id="ask_mcp_tool_call",
                    effect=PolicyEffect.ASK,
                    capability="tool.mcp.call",
                ),
                PolicyRule(
                    rule_id="ask_mcp_server_start",
                    effect=PolicyEffect.ASK,
                    capability="mcp.server.*",
                ),
                PolicyRule(
                    rule_id="ask_subagent_spawn",
                    effect=PolicyEffect.ASK,
                    capability="subagent.*",
                ),
                PolicyRule(
                    rule_id="ask_background_execution",
                    effect=PolicyEffect.ASK,
                    capability="background.*",
                ),
                PolicyRule(
                    rule_id="ask_high_risk",
                    effect=PolicyEffect.ASK,
                    capability="*",
                    # Contained execution is high risk by design; the sandbox
                    # rules above decide it.
                    conditions=PolicyRuleConditions(
                        risk_level=["high", "critical"],
                        exclude_execution_surface=["sandbox"],
                    ),
                ),
            ],
            allow=[
                *_sandbox_allow_rules(),
                PolicyRule(
                    rule_id="allow_local_tools",
                    effect=PolicyEffect.ALLOW,
                    capability="tool.local.call",
                ),
                PolicyRule(
                    rule_id="allow_workspace",
                    effect=PolicyEffect.ALLOW,
                    capability="workspace.*",
                ),
                PolicyRule(
                    rule_id="allow_memory",
                    effect=PolicyEffect.ALLOW,
                    capability="memory.*",
                ),
                PolicyRule(
                    rule_id="allow_telemetry",
                    effect=PolicyEffect.ALLOW,
                    capability="telemetry.*",
                ),
            ],
        ),
    )


def _strict_production_policy() -> PolicyEnvelope:
    return PolicyEnvelope(
        name="strict-production",
        mode=PolicyMode.STRICT,
        profile=PolicyProfile.STRICT_PRODUCTION,
        provenance=PolicyProvenance(source=PolicySource.DEFAULT),
        rules=PolicyRuleSet(
            deny=[
                PolicyRule(
                    rule_id="deny_credential_or_system_prompt_flow",
                    effect=PolicyEffect.DENY,
                    capability="*",
                    conditions=PolicyRuleConditions(
                        data_classes=["credential", "system_prompt"]
                    ),
                    reason="Credential and system prompt data require a brokered boundary.",
                ),
                PolicyRule(
                    rule_id="deny_raw_secret_read",
                    effect=PolicyEffect.DENY,
                    capability="secret.read",
                ),
            ],
            allow=[
                PolicyRule(
                    rule_id="allow_governance_telemetry",
                    effect=PolicyEffect.ALLOW,
                    capability="telemetry.governance.*",
                ),
            ],
        ),
    )
