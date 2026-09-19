"""Durable runs, D2c: a run waits for a person, then continues.

With governance on, an `ask` that nobody answers in the moment pauses the run:
the other calls in the step finish, nothing unapproved runs, and `run()`
returns `awaiting_approval`. After `resolve_approval`, `resume(run_id)` runs
the decided calls (approved, denied with the note, or edited) and continues
the loop. The resumed run sees only its own context, never another request's.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import ScriptedModel, _MODEL


class RecordingModel(ScriptedModel):
    def __init__(self, *turns):
        super().__init__(*turns)
        self.calls: list[list] = []

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) if isinstance(m, dict) else m.model_dump() for m in messages])
        return await super().llm_call(messages, tools, **kwargs)


def _policy():
    policy = build_default_policy("interactive-dev")
    policy.rules.ask.insert(
        0, PolicyRule(rule_id="ask_deletes", effect=PolicyEffect.ASK, capability="workspace.files.delete")
    )
    return policy


async def _agent(tmp_path, model, *, name="keeper", **governance):
    agent = OmniCoreAgent(
        name=name,
        system_instruction="Manage files.",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {"enabled": True, "policy": _policy(), **governance},
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _file(tmp_path, name):
    return tmp_path / "ws" / "files" / name


WRITE_AND_DELETE = [
    ("w1", "write_file", json.dumps({"path": "keep.txt", "content": "kept"})),
    ("w2", "write_file", json.dumps({"path": "old.txt", "content": "old"})),
]
DELETE = [("d1", "delete_file", json.dumps({"path": "old.txt"}))]


def _tool_messages(call):
    return [m for m in call if m.get("role") == "tool"]


@pytest.mark.asyncio
async def test_an_unanswered_ask_pauses_the_run_and_nothing_unapproved_runs(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, [*DELETE, ("w3", "write_file", '{"path": "x.txt", "content": "x"}')], "never")
    agent = await _agent(tmp_path, model)

    result = await agent.run("tidy up", session_id="s1")

    assert result["status"] == "awaiting_approval"
    (approval,) = result["approvals"]
    assert approval["tool_name"] == "delete_file" and approval["capability"] == "workspace.files.delete"
    assert _file(tmp_path, "old.txt").exists(), "the delete must not have run"
    assert _file(tmp_path, "x.txt").exists(), "the other call in the step finished"
    assert len(model.calls) == 2, "no model call after the pause"
    run = await agent.get_run(result["run_id"])
    assert run["status"] == "awaiting_approval"
    stored = await agent.memory_router.get_messages("s1")
    assert "d1" not in {m["metadata"].get("tool_call_id") for m in stored if m["role"] == "tool"}


@pytest.mark.asyncio
async def test_an_approved_call_runs_on_resume_and_the_run_finishes(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "cleaned up")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="s2")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success" and result["response"] == "cleaned up"
    assert result["run_id"] == paused["run_id"]
    assert not _file(tmp_path, "old.txt").exists()
    run = await agent.get_run(paused["run_id"])
    assert run["status"] == "completed"
    assert len(run["trace_ids"]) == 2
    assert {c["tool_call_id"]: c["state"] for c in run["tool_calls"]}["d1"] == "completed"
    # The resumed model call sees the delete's result, once, after its call.
    resumed_call = model.calls[-1]
    assert [m["tool_call_id"] for m in _tool_messages(resumed_call)].count("d1") == 1


@pytest.mark.asyncio
async def test_a_denied_call_tells_the_model_why(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "left it")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="s3")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="deny", approver="bob", note="archive it instead"
    )
    await agent.resume(paused["run_id"])

    assert _file(tmp_path, "old.txt").exists()
    denial = next(m for m in _tool_messages(model.calls[-1]) if m["tool_call_id"] == "d1")
    assert "archive it instead" in json.dumps(denial) and "bob" in json.dumps(denial)


@pytest.mark.asyncio
async def test_approving_an_edited_call_runs_the_edited_call(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, [("d1", "delete_file", json.dumps({"path": "keep.txt"}))], "done")
    agent = await _agent(tmp_path, model, name="named-agent")
    paused = await agent.run("tidy up", session_id="s4")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="approve", approver="alice",
        arguments={"path": "old.txt"},
    )
    await agent.resume(paused["run_id"])

    assert _file(tmp_path, "keep.txt").exists()
    assert not _file(tmp_path, "old.txt").exists()


@pytest.mark.asyncio
async def test_a_resumed_run_never_sees_another_requests_messages(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "other answer", "cleaned up")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="shared")
    (approval,) = paused["approvals"]

    other = await agent.run("an unrelated question", session_id="shared")
    assert other["response"] == "other answer"
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    await agent.resume(paused["run_id"])

    resumed_input = json.dumps(model.calls[-1])
    assert "an unrelated question" not in resumed_input and "other answer" not in resumed_input
    assert "tidy up" in resumed_input


@pytest.mark.asyncio
async def test_a_run_cannot_resume_while_waiting_or_after_it_finished(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "done")
    agent = await _agent(tmp_path, model)
    paused = await agent.run("tidy up", session_id="s5")
    (approval,) = paused["approvals"]

    with pytest.raises(ValueError, match="waiting"):
        await agent.resume(paused["run_id"])
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="a")
    await agent.resume(paused["run_id"])
    with pytest.raises(ValueError, match="completed"):
        await agent.resume(paused["run_id"])
    with pytest.raises(LookupError):
        await agent.resume("run_nope")


@pytest.mark.asyncio
async def test_approval_mode_fail_keeps_the_old_behaviour(tmp_path):
    model = RecordingModel(WRITE_AND_DELETE, DELETE, "could not delete")
    agent = await _agent(tmp_path, model, approval_mode="fail")

    result = await agent.run("tidy up", session_id="s6")

    assert result["status"] == "success" and result["response"] == "could not delete"
    assert _file(tmp_path, "old.txt").exists()
    refusal = next(m for m in _tool_messages(model.calls[-1]) if m["tool_call_id"] == "d1")
    assert "Approval required" in json.dumps(refusal) or "approval" in json.dumps(refusal).lower()


def test_approval_mode_is_validated():
    from omnicoreagent.core.runtime.config import AgentConfig

    with pytest.raises(ValueError, match="approval_mode"):
        AgentConfig(governance_config={"enabled": True, "approval_mode": "later"})
