"""A folder operation that covers a file under an ask rule asks a person once.

The 0.5.0 known issue (0.5.1, B5): deleting, moving or clearing a folder that
held a file covered by an *ask* rule was refused, and the message said the
policy "does not allow" it, though a person could have allowed it. The
operation now asks once, naming the covered files; approving it lets the
operation run, and the covered files count as approved for that operation
only. A *deny* rule on a file inside still refuses the whole operation.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


def _rule(effect, path, capability="workspace.files.delete", rule_id="r"):
    return PolicyRule(rule_id=rule_id, effect=effect, capability=capability, target={"path": path})


async def _agent(tmp_path, model, *, ask=(), deny=(), **governance):
    policy = build_default_policy("permissive-dev")
    policy.rules.ask[:0] = list(ask)
    policy.rules.deny[:0] = list(deny)
    agent = OmniCoreAgent(
        name="keeper",
        system_instruction="Manage files.",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {"enabled": True, "policy": policy, **governance},
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _files(tmp_path, folder="docs", names=("a.txt", "secret.txt"), content="x"):
    root = tmp_path / "ws" / "files" / folder
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_text(content)
    return tmp_path / "ws" / "files"


def _told(model):
    return {m.get("tool_call_id"): m["content"] for m in model.calls[-1] if m.get("tool_call_id")}


DELETE_DOCS = [("d1", "delete_file", json.dumps({"path": "docs"}))]
ASK_SECRET = [_rule(PolicyEffect.ASK, "docs/secret*", rule_id="ask_secret")]


@pytest.mark.asyncio
async def test_a_folder_delete_with_one_ask_covered_file_asks_once_then_deletes_everything(tmp_path):
    files = _files(tmp_path)
    model = RecordingModel(DELETE_DOCS, "gone")
    agent = await _agent(tmp_path, model, ask=ASK_SECRET)

    paused = await agent.run("clear docs", session_id="s1")

    assert paused["status"] == "awaiting_approval"
    (approval,) = paused["approvals"]
    assert (files / "docs" / "secret.txt").exists() and (files / "docs" / "a.txt").exists()
    # The person sees which files the one question covers.
    assert approval["covered_files"] == ["docs/secret.txt"]

    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume(paused["run_id"])

    assert result["status"] == "success"
    assert not (files / "docs").exists(), _told(model)
    run = await agent.get_run(paused["run_id"])
    assert len(run["approvals"]) == 1, "the operation asked once"
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_denied_folder_approval_leaves_everything_intact(tmp_path):
    files = _files(tmp_path)
    model = RecordingModel(DELETE_DOCS, "left it")
    agent = await _agent(tmp_path, model, ask=ASK_SECRET)
    paused = await agent.run("clear docs", session_id="s2")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="deny", approver="bob", note="keep them"
    )
    await agent.resume(paused["run_id"])

    assert (files / "docs" / "secret.txt").exists() and (files / "docs" / "a.txt").exists()
    assert "keep them" in _told(model)["d1"]
    await agent.cleanup()


@pytest.mark.asyncio
async def test_an_approved_folder_move_and_clear_run_too(tmp_path):
    files = _files(tmp_path)
    model = RecordingModel(
        [("m1", "move_file", json.dumps({"old_path": "docs", "new_path": "archive"}))],
        [("c1", "clear_files", json.dumps({}))],
        "done",
    )
    agent = await _agent(
        tmp_path,
        model,
        ask=[_rule(PolicyEffect.ASK, "*secret*", capability="workspace.files.*", rule_id="ask_any_secret")],
    )
    paused = await agent.run("tidy", session_id="s3")
    (approval,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], approval["approval_id"], decision="approve", approver="alice")
    paused = await agent.resume(paused["run_id"])

    assert (files / "archive" / "secret.txt").exists() and not (files / "docs").exists()
    # The clear is a new operation: the first approval does not carry over.
    assert paused["status"] == "awaiting_approval"
    (second,) = paused["approvals"]
    await agent.resolve_approval(paused["run_id"], second["approval_id"], decision="approve", approver="alice")
    await agent.resume(paused["run_id"])
    assert not (files / "archive").exists()
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_deny_covered_file_refuses_the_whole_operation_and_asks_nobody(tmp_path):
    files = _files(tmp_path, names=("a.txt", "secret.txt", "keep.txt"))
    model = RecordingModel(DELETE_DOCS, "refused")
    agent = await _agent(
        tmp_path,
        model,
        ask=ASK_SECRET,
        deny=[_rule(PolicyEffect.DENY, "docs/keep*", rule_id="keep_docs")],
    )

    result = await agent.run("clear docs", session_id="s4")

    assert result["status"] != "awaiting_approval", "a refusal is not put to a person"
    assert all((files / "docs" / n).exists() for n in ("a.txt", "secret.txt", "keep.txt"))
    assert "Governance denied" in _told(model)["d1"] or "denied" in _told(model)["d1"]
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_folder_that_cannot_ask_says_it_needs_approval_not_that_it_is_disallowed(tmp_path):
    files = _files(tmp_path)
    model = RecordingModel(DELETE_DOCS, "refused")
    agent = await _agent(tmp_path, model, ask=ASK_SECRET, approval_mode="fail")

    await agent.run("clear docs", session_id="s5")

    told = _told(model)["d1"]
    assert (files / "docs" / "secret.txt").exists()
    assert "does not allow" not in told
    assert "needs a person's approval" in told
    await agent.cleanup()


@pytest.mark.asyncio
async def test_the_covered_files_are_listed_with_a_cap(tmp_path):
    names = [f"secret{i:02}.txt" for i in range(30)]
    _files(tmp_path, names=names)
    model = RecordingModel(DELETE_DOCS, "gone")
    agent = await _agent(tmp_path, model, ask=ASK_SECRET)

    paused = await agent.run("clear docs", session_id="s6")

    (approval,) = paused["approvals"]
    text = json.dumps(approval)
    assert "docs/secret00.txt" in text and "docs/secret29.txt" not in text
    assert "and 20 more" in text
    # The list and the target are in sorted order, whatever order the file
    # system walks in (it differs between hosts; the 0.5.1 server suite).
    assert approval["covered_files"][0] == "docs/secret00.txt"
    assert approval["target"]["path"] == "docs/secret00.txt", approval["target"]
    await agent.cleanup()
