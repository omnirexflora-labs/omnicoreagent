"""An application says what each run's sandbox is: ``governance_config.sandbox_manifest``.

Found by the repository steward (production proving, P2): its sandbox must
clone a repository, and the docs promised "no network unless your policy
allows it" — but nothing let an application ask for the network, an image, or
a working directory for the sandbox the ``execute`` tool uses. The manifest
is the same one the sandbox layer already understands; the policy still
decides whether what it asks for is allowed.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from omnicoreagent.sandbox import LocalTestSandboxRuntime, SandboxExecResult

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = list(turns)

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn), finish_reason="tool_calls", usage=usage
        )


def _runtime(seen: dict) -> LocalTestSandboxRuntime:
    runtime = LocalTestSandboxRuntime()

    def sh(request, context):
        seen["network"] = context.network_policy.default.value
        seen["working_dir"] = context.session.manifest.working_dir
        seen["image"] = context.session.manifest.image
        return SandboxExecResult(exit_code=0, stdout="ok")

    runtime.register_command("sh", sh)
    return runtime


def _policy(*, network: bool):
    """permissive-dev asks before the sandbox network goes on; a policy that
    allows it says so instead (an ask outranks an allow)."""
    policy = build_default_policy("permissive-dev")
    if network:
        policy.rules.ask = [r for r in policy.rules.ask if r.capability != "sandbox.network.configure"]
        policy.rules.allow.insert(
            0,
            PolicyRule(
                rule_id="allow_sandbox_network",
                effect=PolicyEffect.ALLOW,
                capability="sandbox.network.configure",
            ),
        )
    return policy


async def _agent(model, runtime, *, network: bool, manifest: dict | None):
    governance = {
        "enabled": True,
        "policy": _policy(network=network),
        "sandbox_runtime": runtime,
        "allow_test_sandbox_runtime": True,
    }
    if manifest is not None:
        governance["sandbox_manifest"] = manifest
    agent = OmniCoreAgent(
        name="builder",
        system_instruction="Use execute.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": governance,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


RUN_ONE_COMMAND = [("c1", "execute", '{"command": "echo hi"}')]


@pytest.mark.asyncio
async def test_the_manifest_shapes_the_run_sandbox_and_is_recorded():
    seen: dict = {}
    agent = await _agent(
        ScriptedModel(RUN_ONE_COMMAND, "done"),
        _runtime(seen),
        network=True,
        manifest={
            "network_policy": {"default": "allow"},
            "working_dir": "/home/user/work",
            "image": "python:3.12",
        },
    )

    result = await agent.run("build", session_id="s1")

    assert result["response"] == "done"
    assert seen == {"network": "allow", "working_dir": "/home/user/work", "image": "python:3.12"}
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    created = next(e for e in trace.events if e.event_type == "sandbox_session_created")
    assert created.metadata["network"] == "allow"
    assert created.metadata["working_dir"] == "/home/user/work"
    assert created.metadata["image"] == "python:3.12"


@pytest.mark.asyncio
async def test_without_a_manifest_the_sandbox_is_the_default_one():
    seen: dict = {}
    agent = await _agent(ScriptedModel(RUN_ONE_COMMAND, "done"), _runtime(seen), network=False, manifest=None)

    result = await agent.run("build", session_id="s1")

    assert result["response"] == "done"
    assert seen == {"network": "deny", "working_dir": "/workspace", "image": None}


@pytest.mark.asyncio
async def test_the_policy_still_decides_what_the_manifest_asks_for():
    """A manifest turning the network on under a policy that asks for it: the
    run pauses on that approval before any sandbox exists, as the docs say."""
    seen: dict = {}
    agent = await _agent(
        ScriptedModel(RUN_ONE_COMMAND, "done"),
        _runtime(seen),
        network=False,
        manifest={"network_policy": {"default": "allow"}},
    )

    result = await agent.run("build", session_id="s1")

    assert result["status"] == "awaiting_approval"
    (approval,) = result["approvals"]
    assert approval["capability"] == "sandbox.network.configure"
    assert approval["tool_name"] == "execute"
    assert seen == {}, "no command ran and no sandbox was opened"

    await agent.resolve_approval(result["run_id"], approval["approval_id"], decision="approve", approver="a")
    resumed = await agent.resume(result["run_id"])

    assert resumed["response"] == "done"
    assert seen["network"] == "allow"


@pytest.mark.parametrize(
    ("manifest", "problem"),
    [
        ({"provider": "docker"}, "provider"),
        ({"sandbox_id": "mine"}, "sandbox_id"),
        ({"bogus": 1}, "bogus"),
        ({"network_policy": {"default": "sometimes"}}, "sometimes"),
        ("allow", "must be a dict"),
    ],
)
def test_a_manifest_that_cannot_be_read_is_refused_at_startup(manifest, problem):
    with pytest.raises(ValueError, match=problem):
        OmniCoreAgent(
            name="builder",
            system_instruction="Use execute.",
            model_config=_MODEL,
            agent_config={
                "governance_config": {
                    "enabled": True,
                    "profile": "permissive-dev",
                    "sandbox_config": {"provider": "none"},
                    "sandbox_manifest": manifest,
                }
            },
        )
