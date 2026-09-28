"""The file tools can work in a directory of your choosing.

Found writing the Harbor page (D8): in a trial, the file tools worked in the
agent's own workspace, not the task's directory; the model tried
"Path not found: /app" three times before falling back to commands. The
maintainer's decision (2026-09-28): the task sets the directory, and the
agent's files are there. `workspace_config["files_dir"]` points the file
tools at it; the runtime's own files (traces, offloaded results) stay in
`workspace_dir`, out of the task's directory.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.manager import Workspace
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def test_files_dir_moves_the_file_area_and_only_it(tmp_path):
    task, state = tmp_path / "task", tmp_path / "agent-state"
    workspace = Workspace.from_config(
        WorkspaceConfig(workspace_dir=str(state), files_dir=str(task))
    )
    assert workspace.files.root.resolve() == task.resolve()
    assert state.resolve() in workspace.artifacts.root.resolve().parents


def test_files_dir_is_for_a_local_workspace(tmp_path):
    with pytest.raises(ValueError, match="files_dir"):
        WorkspaceConfig(workspace_backend="s3", s3_bucket="b", files_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_the_agents_file_tools_see_the_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = tmp_path / "app"
    task.mkdir()
    (task / "average.py").write_text("print(1)\n")
    agent = OmniCoreAgent(
        name="solver",
        system_instruction="x",
        model_config=MODEL,
        agent_config={
            "guardrail_mode": "off",
            "workspace_config": {"workspace_dir": str(tmp_path / "state"), "files_dir": str(task)},
        },
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(("c1", "ls", json.dumps({"path": "."})))
    result = await agent.run("look around")
    trajectory = await agent.get_trajectory(result["trace_id"])
    await agent.cleanup()

    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "success"
    assert "average.py" in json.dumps(call["result"])
    assert not (task / "telemetry").exists(), "the runtime's files stay out of the task"
