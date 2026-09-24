"""The agent a Harbor-shaped trial runs: commands go to this machine.

The task container is the boundary, so the sandbox provider is ``local``:
commands run as processes here, in the task's own directory. Policy still
governs every one of them — they are authorized as ``process.exec`` on the
``host`` surface — so the trial allows exactly that and nothing else is
implied.
"""

from __future__ import annotations

import os

from omnicoreagent import OmniCoreAgent
from omnicoreagent.governance import (
    PolicyEffect,
    PolicyRule,
    PolicyRuleConditions,
    build_default_policy,
)

TASK_DIR = os.environ.get("TRIAL_TASK_DIR", "/trial/task")
MODEL = os.environ.get("TRIAL_MODEL", "gpt-5.6-terra")


def _policy():
    """A profile, with host commands allowed and the asks that block them gone."""
    policy = build_default_policy("interactive-dev")
    policy.rules.ask = [
        rule
        for rule in policy.rules.ask
        if rule.rule_id
        not in {"ask_process_exec", "ask_high_risk", "ask_sandbox_network"}
    ]
    policy.rules.allow.insert(
        0,
        PolicyRule(
            rule_id="allow_host_commands",
            effect=PolicyEffect.ALLOW,
            capability="process.exec",
            conditions=PolicyRuleConditions(execution_surface="host"),
        ),
    )
    return policy


agent = OmniCoreAgent(
    name="trial",
    system_instruction=(
        "You fix code in the working directory. Use the execute tool to run "
        "shell commands: read files, change them, and run the tests. Keep "
        "going until the tests pass. Do not change the tests."
    ),
    model_config={
        "provider": "openai",
        "model": MODEL,
        "api_key": os.environ["LLM_API_KEY"],
        "temperature": 0.2,
        "max_context_length": 30000,
    },
    agent_config={
        "max_steps": 20,
        "tool_call_timeout": 120,
        "enable_workspace_files": True,
        # Outside the task's directory on purpose: the workspace defaults to
        # ./workspace, which with the CLI's working directory inside the task
        # would appear among the files a harness verifies.
        "workspace_config": {
            "workspace_dir": os.environ.get("TRIAL_OUT", "/trial/out") + "/agent-workspace"
        },
        "governance_config": {
            "enabled": True,
            "policy": _policy(),
            "sandbox_config": {"provider": "local"},
            "sandbox_manifest": {
                "working_dir": TASK_DIR,
                "network_policy": {"default": "allow"},
                "filesystem_policy": {"default": "allow"},
            },
        },
    },
    telemetry_config={
        "capture": "full",
        "storage": "jsonl",
        # The trial reads this file back as its evidence.
        "storage_path": os.environ.get("TRIAL_OUT", "/trial/out") + "/traces.jsonl",
        "retention_days": None,
    },
)
