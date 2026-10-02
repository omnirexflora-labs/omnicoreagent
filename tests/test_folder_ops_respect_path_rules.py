"""A folder is deleted, moved or cleared only if each file under it could be,
and a path through a link is refused.

The rc7 security review (S1-1): a deny on deleting prod/* refused deleting
prod/db.txt but allowed delete_file("prod"), which removed the folder, and a
deny on secrets/* allowed move_file("secrets", "loot"): `prod` does not match
`prod/*`. S3-1: the policy decides on the path as named, and storage followed
a link inside the root, so pub -> secret let a read of pub/key past secret/*.
"""

from __future__ import annotations

import json
import os

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


def _deny(rule_id, capability, path):
    return PolicyRule(rule_id=rule_id, effect=PolicyEffect.DENY,
                      capability=capability, target={"path": path})


async def _run(tmp_path, rules, calls, setup, *then):
    """``calls`` are one turn; ``then`` are further turns, run after it."""
    policy = build_default_policy("permissive-dev")
    policy.rules.deny[:0] = rules
    model = RecordingModel(calls, *then, "done")
    agent = OmniCoreAgent(
        name="files", system_instruction="x", model_config=_MODEL,
        agent_config={"guardrail_mode": "off",
                      "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
                      "governance_config": {"policy": policy}},
    )
    await agent.initialize()
    agent.llm_connection = model
    files = tmp_path / "ws" / "files"
    setup(files)
    try:
        await agent.run("go", session_id="s")
    finally:
        await agent.cleanup()
    told = {m.get("tool_call_id"): m["content"] for m in model.calls[-1] if m.get("tool_call_id")}
    return files, told


@pytest.mark.asyncio
async def test_a_folder_with_a_protected_file_is_not_deleted_or_moved(tmp_path):
    def setup(files):
        for folder, name in (("prod", "db.txt"), ("secrets", "api_key.txt"), ("scratch", "a.txt")):
            (files / folder).mkdir(parents=True, exist_ok=True)
            (files / folder / name).write_text("x")

    files, told = await _run(
        tmp_path,
        [_deny("keep_prod", "workspace.files.delete", "prod/*"),
         _deny("keep_secrets", "workspace.*", "secrets/*")],
        [("d1", "delete_file", json.dumps({"path": "prod"})),
         ("m1", "move_file", json.dumps({"old_path": "secrets", "new_path": "loot"})),
         ("c1", "clear_files", json.dumps({})),
         ("d2", "delete_file", json.dumps({"path": "scratch"}))],
        setup,
    )

    assert (files / "prod" / "db.txt").exists(), told["d1"]
    assert (files / "secrets" / "api_key.txt").exists() and not (files / "loot").exists(), told["m1"]
    assert "prod/db.txt" in told["d1"] or "secrets" in told["c1"]
    assert not (files / "scratch").exists(), "a folder with nothing protected is still deleted"


@pytest.mark.asyncio
async def test_a_path_through_a_link_is_refused(tmp_path):
    def setup(files):
        (files / "secret").mkdir(parents=True, exist_ok=True)
        (files / "secret" / "key.txt").write_text("TOPSECRET-L-555")
        os.symlink(files / "secret", files / "pub")

    _, told = await _run(
        tmp_path,
        [_deny("no_secret", "workspace.*", "secret/*")],
        [("r1", "read_file", json.dumps({"path": "pub/key.txt"}))],
        setup,
    )

    assert "TOPSECRET-L-555" not in told["r1"]
    assert "link" in told["r1"]
    # The rc7 gate (B7-5): the refusal came back as a successful read.
    assert '"status": "error"' in told["r1"] or "'status': 'error'" in told["r1"], told["r1"]


@pytest.mark.asyncio
async def test_a_link_in_the_workspace_does_not_break_listing_or_search(tmp_path):
    # The rc7 gate (B7-4): one link inside the workspace made ls, grep, glob
    # and clear_files fail wholesale with "goes through a link".
    def setup(files):
        (files / "notes").mkdir(parents=True, exist_ok=True)
        (files / "notes" / "a.txt").write_text("alpha")
        os.symlink(files / "notes", files / "link")

    files, told = await _run(
        tmp_path,
        [],
        [("l1", "ls", json.dumps({"path": "."})),
         ("g1", "grep", json.dumps({"pattern": "alpha"})),
         ("g2", "glob", json.dumps({"pattern": "**/*.txt"}))],
        setup,
        [("c1", "clear_files", json.dumps({}))],
    )
    assert "notes" in told["l1"] and "goes through a link" not in told["l1"]
    assert "notes/a.txt" in told["g1"] and "notes/a.txt" in told["g2"]
    assert "goes through a link" not in told["c1"]
    assert not (files / "notes").exists() and not (files / "link").exists()
