"""C4: command rules apply where every command passes, and approvals show and
bind the command.

`_sandbox_authority_request` is the one function every command passes through
before it runs: the host `local` provider, every sandbox provider, skill
scripts and Harbor trials. The command is parsed there. Found checking the gap
(2026-09-29): an approval for a command on the host showed `sh` and an argument
count, and its digest left the command out, so approving `ls` looked the same
as approving `rm -rf ~`.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.run_approvals import request_digest
from omnicoreagent.governance.commands import approval_metadata
from omnicoreagent.governance import GovernanceEngine, PolicyEffect, PolicyRule, PolicyRuleConditions
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.sandbox import SandboxCommandSpec, SandboxExecutionService
from omnicoreagent.sandbox.execution import _sandbox_authority_request
from test_local_sandbox import ALLOW_SETUP, _manifest, _policy, _runtime

DENY_RM = PolicyRule(
    rule_id="deny_recursive_rm", effect=PolicyEffect.DENY, capability="process.exec",
    command={"program": "rm", "args_any": ["-r", "-R", "--recursive"]},
)
ALLOW_HOST = PolicyRule(
    rule_id="allow_host_commands", effect=PolicyEffect.ALLOW, capability="process.exec",
    conditions=PolicyRuleConditions(execution_surface="host"),
)


def _engine(*rules):
    policy = _policy(ALLOW_SETUP, ALLOW_HOST)
    policy.rules.deny = list(rules)
    return GovernanceEngine(policy, sandbox_runtime=_runtime())


@pytest.mark.asyncio
async def test_a_command_rule_stops_a_real_command_on_the_host(tmp_path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "app").write_text("keep")
    service = SandboxExecutionService(_engine(DENY_RM))

    with pytest.raises(GovernanceError) as refused:
        await service.execute(SandboxCommandSpec(command=["sh", "-c", "ls && rm -rf build"], manifest=_manifest(tmp_path)))

    assert "deny_recursive_rm" in str(refused.value.metadata) or "deny_recursive_rm" in str(refused.value)
    assert (tmp_path / "build" / "app").read_text() == "keep"

    ran = await service.execute(SandboxCommandSpec(command=["sh", "-c", "echo fine > out.txt"], manifest=_manifest(tmp_path)))
    assert ran.ok and (tmp_path / "out.txt").read_text() == "fine\n"


def test_the_request_shows_what_would_run_without_a_field_for_traces():
    request = _sandbox_authority_request(SandboxCommandSpec(command=["sh", "-c", "git status && git push origin main"]), "host")

    command = request.metadata["command"]
    assert command["name"] == "sh" and command["argc"] == 3
    assert command["programs"] == ["git", "git"]
    assert command["opaque"] is False
    # Arguments stay off the request's metadata (events record it as it is)...
    assert "origin main" not in str(request.metadata)
    # ...and go on the approval a person reads.
    assert approval_metadata(request)["command"]["summary"] == ["git status", "git push origin main"]
    # The parse is not a field of the request, so nothing serializes it whole.
    from dataclasses import fields

    assert "command" not in {f.name for f in fields(request)}


def test_an_approval_is_bound_to_the_exact_command():
    def digest(text):
        return request_digest(_sandbox_authority_request(SandboxCommandSpec(command=["sh", "-c", text]), "host"))

    assert digest("ls") == digest("ls")
    assert digest("ls") != digest("rm -rf ~")
    assert digest("ls") != digest("ls ")


def test_approvals_of_everything_else_keep_their_digest():
    # A pending tool approval recorded before this change must still match.
    from omnicoreagent.governance.models import AuthorityRequest

    tool = AuthorityRequest(capability="tool.local.call", target={"tool_name": "issue_refund"},
                            metadata={"tool_name": "issue_refund", "arguments_digest": "abc"})
    # The digest 0.4.3 computed for this approval, pinned.
    assert request_digest(tool) == "714e7239a790aaa7266e27b6b748ec221a9e4bbc1cc6438233c742ac39ffd8b0"


@pytest.mark.asyncio
async def test_a_person_asked_to_approve_sees_the_command(tmp_path):
    seen = []

    class Recorder:
        async def resolve(self, approval):
            seen.append(approval)
            from omnicoreagent.governance.models import ApprovalResult

            return ApprovalResult(approved=False, approval_id=approval.approval_id, resolved_by="test")

    ask_push = PolicyRule(rule_id="ask_git_push", effect=PolicyEffect.ASK, capability="process.exec",
                          command={"prefix": ["git", "push"]})
    policy = _policy(ALLOW_SETUP, ALLOW_HOST)
    policy.rules.ask = [ask_push]
    engine = GovernanceEngine(policy, sandbox_runtime=_runtime(), approval_resolver=Recorder())

    with pytest.raises(GovernanceError):
        await SandboxExecutionService(engine).execute(
            SandboxCommandSpec(command=["sh", "-c", "git status;" + " " * 200 + "git push --force"], manifest=_manifest(tmp_path))
        )

    shown = seen[0].metadata["command"]
    assert shown["summary"] == ["git status", "git push --force"]
    assert seen[0].target.resource == "sh"


@pytest.mark.asyncio
async def test_an_agent_on_the_host_is_stopped_by_a_command_rule_by_name(tmp_path):
    # The scene the recording could not film honestly: an agent that may run
    # commands on this machine, asked to rm -rf, stopped by a rule naming rm.
    import json

    from test_execute_tool import ScriptedModel
    from test_local_sandbox import _agent, _agent_policy

    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "app").write_text("keep")
    policy = _agent_policy()
    policy.rules.deny.insert(0, DENY_RM)
    model = ScriptedModel(
        [("c1", "execute", '{"command": "rm -rf build && make"}')],
        [("c2", "execute", '{"command": "ls build"}')],
        "done",
    )
    agent = await _agent(model, tmp_path, policy=policy)

    result = await agent.run("Clean the build folder and rebuild.", session_id="rm")

    assert (tmp_path / "build" / "app").read_text() == "keep"
    run = await agent.get_run(result["run_id"])
    outcomes = {c["tool_call_id"]: c["outcome"] for c in run["tool_calls"]}
    assert outcomes == {"c1": "error", "c2": "success"}
    trace = json.dumps(await agent.telemetry_store.get_trace(result["trace_id"]), default=str)
    assert "deny_recursive_rm" in trace
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_paused_run_shows_the_approver_the_command(tmp_path):
    # What a person reads (get_run, OmniServe's /runs/{id}) names the commands,
    # not `sh` and an argument count.
    from test_execute_tool import ScriptedModel
    from test_local_sandbox import _agent, _agent_policy

    policy = _agent_policy()
    policy.rules.ask.insert(0, PolicyRule(rule_id="ask_git_push", effect=PolicyEffect.ASK,
                                          capability="process.exec", command={"prefix": ["git", "push"]}))
    model = ScriptedModel([("c1", "execute", '{"command": "git status;   git push origin main"}')], "done")
    agent = await _agent(model, tmp_path, policy=policy)

    paused = await agent.run("Publish it.", session_id="push")

    assert paused["status"] == "awaiting_approval"
    approval = (await agent.get_run(paused["run_id"]))["approvals"][0]
    assert approval["command"]["summary"] == ["git status", "git push origin main"]
    assert approval["command"]["programs"] == ["git", "git"]
    # And where a person actually reads it: run()'s own result, and OmniServe's
    # view (the 0.5.0rc1 gate found both left it out).
    assert paused["approvals"][0]["command"]["summary"] == ["git status", "git push origin main"]
    from omnicoreagent.serve.routes.runs import _public_view

    record = await agent.get_run(paused["run_id"])
    view = _public_view(agent, record["approvals"][0], record)
    assert view["command"]["summary"] == ["git status", "git push origin main"]
    assert "decision" in view
    await agent.cleanup()
