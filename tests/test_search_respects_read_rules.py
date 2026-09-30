"""grep, glob and ls do not reveal files a read rule protects.

The 0.5.0rc6 gate: with a deny rule on reading secret/*, read_file was
refused, but grep returned the protected file's contents and glob and ls its
name: the policy was checked on the folder searched, never on the files read.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


@pytest.mark.asyncio
async def test_a_protected_file_is_not_searched_or_listed(tmp_path):
    policy = build_default_policy("permissive-dev")
    policy.rules.deny.insert(0, PolicyRule(
        rule_id="no_secret_reads", effect=PolicyEffect.DENY,
        capability="workspace.files.read", target={"path": "secret/*"}))
    model = RecordingModel(
        [("g1", "grep", json.dumps({"pattern": "TOPSECRET"})),
         ("g2", "glob", json.dumps({"pattern": "**/*.txt"})),
         ("l1", "ls", json.dumps({"path": "secret"}))],
        "done",
    )
    agent = OmniCoreAgent(
        name="searcher", system_instruction="x", model_config=_MODEL,
        agent_config={"guardrail_mode": "off",
                      "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
                      "governance_config": {"policy": policy}},
    )
    await agent.initialize()
    agent.llm_connection = model
    files = tmp_path / "ws" / "files"
    (files / "secret").mkdir(parents=True, exist_ok=True)
    (files / "secret" / "key.txt").write_text("TOPSECRET-G-777")
    (files / "notes.txt").write_text("TOPSECRET is not here, only its name")

    await agent.run("search", session_id="s")

    told = {m.get("tool_call_id"): m["content"] for m in model.calls[-1] if m.get("tool_call_id")}
    assert "TOPSECRET-G-777" not in json.dumps(told)
    assert "key.txt" not in told["g2"] and "key.txt" not in told["l1"]
    assert "notes.txt" in told["g1"], "readable files are still searched"
    await agent.cleanup()
