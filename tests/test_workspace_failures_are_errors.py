"""A file tool that failed is recorded as an error (Build stranger test,
2026-09-27): "File not found: plan.md" came back with outcome success, so
neither the trajectory nor the model's result said the read had failed."""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from test_run_suspend import RecordingModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("read_file", '{"path": "missing.md"}', "File not found: missing.md"),
        ("edit_file", '{"path": "missing.md", "old_str": "a", "new_str": "b"}', "File not found"),
        ("write_file", '{"path": "new.md", "content": "x", "mode": "append"}', "Cannot append"),
    ],
)
async def test_a_failed_file_call_is_an_error(tmp_path, tool, arguments, message):
    agent = OmniCoreAgent(
        name="files", system_instruction="x", model_config=MODEL,
        agent_config={"guardrail_mode": "off", "workspace_config": {"workspace_dir": str(tmp_path / "ws")}},
    )
    await agent.initialize()
    agent.llm_connection = RecordingModel([("c1", tool, arguments)], "done")
    result = await agent.run("go")
    trajectory = await agent.get_trajectory(result["trace_id"])
    await agent.cleanup()

    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "error"
    assert message in str(call["result"]["message"])


@pytest.mark.asyncio
async def test_a_file_call_that_works_is_still_a_success(tmp_path):
    agent = OmniCoreAgent(
        name="files", system_instruction="x", model_config=MODEL,
        agent_config={"guardrail_mode": "off", "workspace_config": {"workspace_dir": str(tmp_path / "ws")}},
    )
    await agent.initialize()
    agent.llm_connection = RecordingModel([("c1", "write_file", '{"path": "plan.md", "content": "x"}')], "done")
    result = await agent.run("go")
    trajectory = await agent.get_trajectory(result["trace_id"])
    await agent.cleanup()
    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "success"
