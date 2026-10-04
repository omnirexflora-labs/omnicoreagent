"""Durable runs, D2b: approvals as records a person decides later.

When governance asks and nobody answers in the moment, the run's resolver
records a pending approval on the run. A person decides it with
`agent.resolve_approval(...)`. A decision applies only to the exact request
it was made for (capability, target, tool, and arguments), once, until it
expires.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
async def test_a_decision_made_in_time_is_honoured_after_the_window():
    """The expiry limits how long a person has to decide, not how long a
    decision lasts (the maintainer's decision, 2026-09-26)."""
    agent, tracker, engine = await _setup(expires_seconds=60)
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]
    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="alice")
    await _past_the_window(agent)
    await tracker.reload()

    decision = await _ask(engine, tracker, _delete("a.txt"))

    assert decision.effect.value == "allow"


@pytest.mark.asyncio
async def test_an_approval_nobody_decided_in_time_is_a_refusal():
    agent, tracker, engine = await _setup(expires_seconds=60)
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    await _past_the_window(agent)
    await tracker.reload()

    with pytest.raises(ApprovalRequiredError, match="expired"):
        await _ask(engine, tracker, _delete("a.txt"))
    (approval,) = (await agent.get_run("run_approve"))["approvals"]
    assert approval["status"] == "expired" and approval["decision"] == "deny"


async def _past_the_window(agent):
    record = await agent.memory_router.get_run_state("run_approve")
    for approval in record["approvals"]:
        approval["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    version = record.pop("version")
    await agent.memory_router.save_run_state(record, expected_version=version)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_the_decision_is_on_the_record_as_soon_as_it_is_made(decision):
    # The 0.5.0rc2 gate: `decision` was written only when a resume applied the
    # approval, so the answer to the decide call (and the run record until
    # the resume) showed `"decision": null`.
    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, _delete("a.txt"))
    (pending,) = (await agent.get_run("run_approve"))["approvals"]

    await agent.resolve_approval("run_approve", pending["approval_id"], decision=decision, approver="alice")

    (decided,) = (await agent.get_run("run_approve"))["approvals"]
    assert decided["decision"] == decision
    assert decided["status"] == ("approved" if decision == "approve" else "denied")


@pytest.mark.asyncio
async def test_an_approved_sandbox_network_holds_for_the_rest_of_the_run():
    # The 0.5.0rc2 gate: a run whose sandbox network was approved paused later
    # for another approval; the resume opened a new sandbox session, which
    # asked about the same network again. Setting up the sandbox is repeated
    # for each session, so its approval holds for the run; a tool call's
    # approval is still spent once.
    from omnicoreagent.sandbox.execution import _sandbox_scope_request

    def network():
        return _sandbox_scope_request(
            "sandbox.network.configure", actor="agent", host="*", risk_level="high",
            metadata={"default": "allow", "allowed_hosts": [], "denied_hosts": []},
        )

    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, network())
    (pending,) = (await agent.get_run("run_approve"))["approvals"]
    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="alice")
    await tracker.reload()

    for _ in range(2):  # the first session, then the session after a resume
        decision = await _ask(engine, tracker, network())
        assert decision.effect.value == "allow" or decision.approval_id

    with pytest.raises(ApprovalRequiredError):  # another host is another question
        await _ask(engine, tracker, _sandbox_scope_request(
            "sandbox.network.configure", actor="agent", host="example.com",
            risk_level="medium", metadata={"mode": "allow"}))
    assert len((await agent.get_run("run_approve"))["approvals"]) == 2


@pytest.mark.asyncio
async def test_an_approved_mount_image_and_resources_hold_for_the_rest_of_the_run():
    # The rc7 gate (D F1): a run with a mount and the network on paused on
    # the mount, then the network, then the mount again: only the network
    # and environment approvals held for the run.
    from omnicoreagent.sandbox.execution import _sandbox_scope_request

    def mount():
        return _sandbox_scope_request(
            "sandbox.filesystem.mount", actor="agent", path="/srv/data", risk_level="high",
            metadata={"mode": "read_only"},
        )

    agent, tracker, engine = await _setup()
    with pytest.raises(ApprovalRequiredError):
        await _ask(engine, tracker, mount())
    (pending,) = (await agent.get_run("run_approve"))["approvals"]
    await agent.resolve_approval("run_approve", pending["approval_id"], decision="approve", approver="alice")
    await tracker.reload()

    for _ in range(2):
        decision = await _ask(engine, tracker, mount())
        assert decision.effect.value == "allow" or decision.approval_id
    assert len((await agent.get_run("run_approve"))["approvals"]) == 1


@pytest.mark.asyncio
async def test_a_second_call_asking_the_same_question_waits_on_the_same_approval():
    # The 0.5.0rc3 gate: two execute calls in one turn both needed the
    # sandbox network. Only the first was recorded as waiting; the second was
    # refused and never replayed, though the person approved the network.
    from omnicoreagent.core.runs import waiting_for_approval
    from omnicoreagent.sandbox.execution import _sandbox_scope_request

    def network(call_id):
        request = _sandbox_scope_request(
            "sandbox.network.configure", actor="agent", host="*", risk_level="high",
            metadata={"default": "allow", "allowed_hosts": [], "denied_hosts": []},
        )
        request.metadata["tool_call_id"] = call_id
        return request

    agent, tracker, engine = await _setup()
    for call_id in ("c1", "c2"):
        with pytest.raises(ApprovalRequiredError):
            await _ask(engine, tracker, network(call_id))

    approvals = (await agent.get_run("run_approve"))["approvals"]
    assert len(approvals) == 1, "one question for the person"
    async with tracker.active():
        assert waiting_for_approval("c1") and waiting_for_approval("c2")

    await agent.resolve_approval("run_approve", approvals[0]["approval_id"], decision="approve", approver="alice")
    await tracker.reload()
    for call_id in ("c1", "c2"):  # both run on resume
        decision = await _ask(engine, tracker, network(call_id))
        assert decision.effect.value == "allow" or decision.approval_id
