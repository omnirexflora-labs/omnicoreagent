"""Durable runs, D2b: approvals as records a person decides later.

When governance asks and nobody answers in the moment, the run's resolver
records a pending approval on the run. A person decides it with
`agent.resolve_approval(...)`. A decision applies only to the exact request
it was made for (capability, target, tool, and arguments), once, until it
expires.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from omnicoreagent.core.runs import RunTracker
from omnicoreagent.core.run_approvals import RunApprovalResolver, request_digest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import (
    ApprovalRequiredError,
    GovernanceEngine,
    PolicyConstraints,
    PolicyEffect,
    PolicyRule,
    build_default_policy,
)
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.hashing import attach_policy_hash
from test_execute_tool import _MODEL


def _delete(path: str):
    (request,) = tool_authority_requests(
        tool_name="delete_file", tool_args={"path": path}, tool_provider="workspace"
    )
    return request


def _engine(expires_seconds=None):
    policy = build_default_policy("interactive-dev")
    policy.rules.ask.insert(
        0,
        PolicyRule(
            rule_id="ask_deletes",
            effect=PolicyEffect.ASK,
            capability="workspace.files.delete",
            constraints=PolicyConstraints(approval_expires_seconds=expires_seconds),
        ),
    )
    return GovernanceEngine(attach_policy_hash(policy), approval_resolver=RunApprovalResolver())


async def _setup(**engine_options):
    agent = OmniCoreAgent(
        name="approver",
        system_instruction="x",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    tracker = RunTracker(agent.memory_router, run_id="run_approve", session_id="s", agent_name="approver")
    await tracker.start(None)
    return agent, tracker, _engine(**engine_options)


async def _ask(engine, tracker, request):
    async with tracker.active():
        return await engine.authorize(request)


def test_a_request_digest_binds_capability_target_tool_and_arguments():
    same = request_digest(_approval(_delete("a.txt")))

    assert request_digest(_approval(_delete("a.txt"))) == same
    assert request_digest(_approval(_delete("b.txt"))) != same
    (other_tool,) = tool_authority_requests(
        tool_name="write_file", tool_args={"path": "a.txt"}, tool_provider="workspace"
    )
    assert request_digest(_approval(other_tool)) != same


def _approval(request):
    from omnicoreagent.governance.models import ApprovalRequest

    return ApprovalRequest(
        request_id=request.request_id,
        decision_id="d",
        capability=request.capability,
        actor=request.actor,
        target=request.target,
        provider=request.provider,
        execution_surface=request.execution_surface,
        metadata=dict(request.metadata),
    )


@pytest.mark.asyncio
async def test_an_unanswered_ask_is_recorded_on_the_run():
    agent, tracker, engine = await _setup()

    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))

    (pending,) = (await agent.get_run("run_approve"))["approvals"]
    assert pending["status"] == "pending"
    assert pending["capability"] == "workspace.files.delete"
    assert pending["tool_name"] == "delete_file"
    assert pending["expires_at"]
    assert len(pending["request_digest"]) == 64


@pytest.mark.asyncio
async def test_an_approval_applies_once_to_the_exact_request():
    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]

    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="alice")
    await tracker.reload()

    with pytest.raises(ApprovalRequiredError):  # a different file is a different request
        await _ask(engine, tracker, _delete("b.txt"))
    decision = await _ask(engine, tracker, _delete("a.txt"))
    assert decision.effect.value == "allow" or decision.approval_id
    with pytest.raises(ApprovalRequiredError):  # used once
        await _ask(engine, tracker, _delete("a.txt"))

    approvals = {a["approval_id"]: a for a in (await agent.get_run("run_approve"))["approvals"]}
    assert approvals[pending["approval_id"]]["status"] == "used"
    assert approvals[pending["approval_id"]]["approver"] == "alice"


@pytest.mark.asyncio
async def test_a_denial_carries_the_approvers_note():
    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]

    await agent.resolve_approval(
        "run_approve", pending["approval_id"], decision="deny", approver="bob", note="archive it instead"
    )
    await tracker.reload()

    with pytest.raises(ApprovalRequiredError, match="archive it instead"):
        await _ask(engine, tracker, _delete("a.txt"))


@pytest.mark.asyncio
async def test_an_expired_approval_is_not_honoured(monkeypatch):
    agent, tracker, engine = await _setup(expires_seconds=60)
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]
    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="alice")
    await tracker.reload()

    import omnicoreagent.core.run_approvals as module

    later = module.utc_now() + timedelta(minutes=5)
    monkeypatch.setattr(module, "utc_now", lambda: later)

    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))


@pytest.mark.asyncio
async def test_approving_with_edits_authorizes_only_the_edited_call():
    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("reports/all.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]

    await agent.resolve_approval(
        "run_approve",
        pending["approval_id"],
        decision="approve",
        approver="alice",
        arguments={"path": "reports/old.txt"},
    )
    await tracker.reload()

    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("reports/all.txt"))
    await _ask(engine, tracker, _delete("reports/old.txt"))
    record = await agent.get_run("run_approve")
    edited = next(a for a in record["approvals"] if a["approval_id"] == pending["approval_id"])
    assert edited["edited_arguments"] == {"path": "reports/old.txt"}


@pytest.mark.asyncio
async def test_resolving_an_unknown_or_decided_approval_is_an_error():
    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]

    with pytest.raises(LookupError):
        await agent.resolve_approval("run_approve", "approval_nope", decision="approve", approver="a")
    with pytest.raises(LookupError):
        await agent.resolve_approval("run_nope", pending["approval_id"], decision="approve", approver="a")
    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="a")
    with pytest.raises(ValueError, match="already"):
        await agent.resolve_approval("run_approve", pending["approval_id"], decision="deny", approver="b")
    with pytest.raises(ValueError, match="decision"):
        await agent.resolve_approval("run_approve", pending["approval_id"], decision="maybe", approver="b")
