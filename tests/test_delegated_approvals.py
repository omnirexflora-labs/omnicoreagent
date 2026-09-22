"""A worker's ask pauses its lead; the lead's approval reaches the worker.

Found by P3 of the production proving plan: a governed worker that hit an
`ask` returned `awaiting_approval`, which `spawn_subagents` reported to its
lead as "Subagent encountered an error", leaving the worker's run parked
with nobody to resume it — so the steward had to keep every GitHub write
with the lead. Now the worker's approvals appear on the lead's run (naming
the worker's call, its arguments, and the worker's run), the lead's run
pauses on them, a decision on the lead's approval is forwarded to the
worker's, and when the lead resumes, its delegation resumes the worker's run
from where it stopped instead of starting a new one.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule
from test_run_suspend import _MODEL, RecordingModel, _file, _policy


def _lead_policy(*, spawn: str = "allow"):
    """The dev profile asks before delegating; a lead that may delegate says so."""
    policy = _policy()
    if spawn == "allow":
        policy.rules.ask = [r for r in policy.rules.ask if not r.capability.startswith("subagent")]
        policy.rules.allow.insert(
            0, PolicyRule(rule_id="allow_delegation", effect=PolicyEffect.ALLOW, capability="subagent.*")
        )
    return policy

SPAWN = [("s1", "spawn_subagents", json.dumps({"subagents": [
    {"name": "cleaner", "role": "Cleaner", "task": "tidy up", "output_path": "/workspace/cleaner/out.md"}
]}))]
WORKER_WRITES = [
    ("w1", "write_file", json.dumps({"path": "old.txt", "content": "old"})),
    ("w2", "write_file", json.dumps({"path": "cleaner/out.md", "content": "report"})),
]
WORKER_DELETES = [("d1", "delete_file", json.dumps({"path": "old.txt"}))]


async def _lead(tmp_path, model, *, spawn: str = "allow"):
    lead = OmniCoreAgent(
        name="lead",
        system_instruction="Delegate the tidying.",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_subagents": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {"enabled": True, "policy": _lead_policy(spawn=spawn)},
        },
        telemetry_config={"capture": "full"},
    )
    await lead.initialize()
    lead.llm_connection = model
    return lead


async def _worker(lead, model):
    """A worker as the factory would build it: the lead's memory store and
    telemetry, its own policy (the same asks), the same workspace."""
    worker = OmniCoreAgent(
        name="subagent_cleaner",
        system_instruction="Tidy the workspace.",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": lead.agent_config["workspace_config"],
            "governance_config": {"enabled": True, "policy": _policy()},
        },
        memory_router=lead.memory_router,
        telemetry_store=lead.telemetry_store,
        telemetry_recorder=lead.telemetry_recorder,
    )
    await worker.initialize()
    worker.llm_connection = model
    return worker


@pytest.mark.asyncio
async def test_a_workers_ask_pauses_the_lead_and_the_leads_decision_reaches_the_worker(tmp_path):
    lead_model = RecordingModel(SPAWN, "all tidy")
    worker_model = RecordingModel(WORKER_WRITES, WORKER_DELETES, "worker done")
    lead = await _lead(tmp_path, lead_model)
    worker = await _worker(lead, worker_model)
    lead._subagent_factory.create_subagent = lambda **_: worker

    paused = await lead.run("tidy up", session_id="team")

    assert paused["status"] == "awaiting_approval", paused
    (approval,) = paused["approvals"]
    assert approval["tool_name"] == "delete_file"
    assert approval["arguments"] == {"path": "old.txt"}
    assert approval["delegated_run_id"], "the approval names the worker's run"
    assert _file(tmp_path, "old.txt").exists()
    worker_record = await worker.get_run(approval["delegated_run_id"])
    assert worker_record["status"] == "awaiting_approval"

    await lead.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await lead.resume(paused["run_id"])

    assert result["status"] == "success" and result["response"] == "all tidy"
    assert not _file(tmp_path, "old.txt").exists(), "the worker's approved delete ran"
    worker_record = await worker.get_run(approval["delegated_run_id"])
    assert worker_record["status"] == "completed"
    assert len(worker_record["trace_ids"]) == 2, "the worker's run resumed, it did not start over"
    assert worker_model.calls and len(worker_model.calls) == 3, "one worker, resumed, not a second one"
    lead_record = await lead.get_run(paused["run_id"])
    assert {a["status"] for a in lead_record["approvals"]} == {"approved"}


@pytest.mark.asyncio
async def test_a_denied_workers_ask_is_heard_by_the_worker(tmp_path):
    lead_model = RecordingModel(SPAWN, "done anyway")
    worker_model = RecordingModel(WORKER_WRITES, WORKER_DELETES, "kept it")
    lead = await _lead(tmp_path, lead_model)
    worker = await _worker(lead, worker_model)
    lead._subagent_factory.create_subagent = lambda **_: worker

    paused = await lead.run("tidy up", session_id="team-2")
    (approval,) = paused["approvals"]
    await lead.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="deny", approver="bob", note="archive it instead"
    )
    result = await lead.resume(paused["run_id"])

    assert result["status"] == "success"
    assert _file(tmp_path, "old.txt").exists(), "the denied delete did not run"
    denial = json.dumps(worker_model.calls[-1])
    assert "archive it instead" in denial and "bob" in denial


@pytest.mark.asyncio
async def test_an_ask_on_delegation_itself_pauses_the_lead(tmp_path):
    """The dev profile asks before a lead delegates. That ask, raised inside
    the spawn tool, used to be recorded against no call: the tool errored and
    the run went on. It pauses the run now, and the delegation runs after a
    decision."""
    lead_model = RecordingModel(SPAWN, "delegated")
    worker_model = RecordingModel(WORKER_WRITES, "worker done")
    lead = await _lead(tmp_path, lead_model, spawn="ask")
    worker = await _worker(lead, worker_model)
    lead._subagent_factory.create_subagent = lambda **_: worker

    paused = await lead.run("tidy up", session_id="team-3")

    assert paused["status"] == "awaiting_approval"
    (approval,) = paused["approvals"]
    assert approval["tool_name"] == "spawn_subagents" and approval["capability"] == "subagent.spawn"
    assert not worker_model.calls, "nothing was delegated before the decision"

    await lead.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await lead.resume(paused["run_id"])

    assert result["response"] == "delegated"
    assert len(worker_model.calls) == 2, "the worker ran once the delegation was approved"
    assert _file(tmp_path, "cleaner/out.md").exists()
