"""A worker's output is the file it wrote, not one an earlier run left.

Found by the repository steward's P3 kill rehearsal: its `fix` worker hit
its step limit without writing `subagents/fix.md`, the file from the
previous run was still at that path, and the steward read it and reported
the fix as verified. A worker that completed without writing was also
"verified" whenever any file was at its path. The delegation now compares
the file with what was there before the worker started.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_run_suspend import _MODEL, RecordingModel

SPAWN = [("s1", "spawn_subagents", json.dumps({"subagents": [
    {"name": "fix", "role": "Fixer", "task": "fix it", "output_path": "subagents/fix.md"}
]}))]


async def _lead(tmp_path, lead_model, worker_model):
    lead = OmniCoreAgent(
        name="lead",
        system_instruction="Delegate the fix.",
        model_config=_MODEL,
        local_tools=ToolRegistry(),
        agent_config={
            "guardrail_mode": "off",
            "enable_subagents": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
        },
    )
    await lead.initialize()
    lead.llm_connection = lead_model
    factory = lead._subagent_factory
    create = factory.create_subagent

    def create_with_model(**kwargs):
        worker = create(**kwargs)
        original = worker.initialize

        async def initialize():
            await original()
            worker.llm_connection = worker_model

        worker.initialize = initialize
        return worker

    factory.create_subagent = create_with_model
    return lead


def _stale(tmp_path):
    path = tmp_path / "ws" / "files" / "subagents" / "fix.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# An earlier run's fix\n")
    return path


def _spawn_result(lead_model):
    for message in reversed(lead_model.calls[-1]):
        if message.get("role") == "tool":
            return json.loads(message["content"])
    raise AssertionError("no spawn result reached the lead")


@pytest.mark.asyncio
async def test_a_worker_that_wrote_nothing_is_not_verified_by_an_earlier_file(tmp_path):
    _stale(tmp_path)
    lead_model = RecordingModel(SPAWN, "done")
    worker_model = RecordingModel("I could not finish.")
    lead = await _lead(tmp_path, lead_model, worker_model)

    await lead.run("fix it", session_id="stale-output")

    result = _spawn_result(lead_model)
    assert result["status"] == "error", result
    (item,) = result["data"]["results"]
    assert "before" in item["error"] and "earlier" in item["error"], item


@pytest.mark.asyncio
async def test_a_worker_that_wrote_its_output_is_verified(tmp_path):
    stale = _stale(tmp_path)
    lead_model = RecordingModel(SPAWN, "done")
    worker_model = RecordingModel(
        [("w1", "write_file", json.dumps({"path": "subagents/fix.md", "content": "# This run's fix\n", "mode": "overwrite"}))],
        "written",
    )
    lead = await _lead(tmp_path, lead_model, worker_model)

    await lead.run("fix it", session_id="fresh-output")

    result = _spawn_result(lead_model)
    assert result["status"] == "success", result
    assert stale.read_text() == "# This run's fix\n"
