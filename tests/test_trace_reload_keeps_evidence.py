"""A finished trace read back by a new process is as complete as it was.

Found writing Stores and scale (D7): the log keeps a finished trace's
records until it is compacted, and the archive holds the whole trace. A new
process loading the log brought the archived trace back, then replayed the
log's span records on top of it: each was "Span already exists", counted as
a damaged record, and the trace was marked incomplete, with its evidence
partial. The archive was then rewritten with that. A record already
reflected in the trace is applied once, not counted as damage.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.telemetry import store as store_module
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


@pytest.mark.asyncio
async def test_a_new_process_reads_a_finished_trace_as_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path / "workspace"))
    agent = OmniCoreAgent(
        name="reload",
        system_instruction="x",
        model_config=MODEL,
        agent_config={"guardrail_mode": "off"},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel()
    result = await agent.run("hi")
    before = await agent.get_telemetry_trace(result["trace_id"])
    await agent.cleanup()
    assert before["evidence_status"] == "complete" and not before["incomplete"]

    # A new process: a fresh store object over the same log and archive.
    log = tmp_path / "workspace" / "telemetry" / "traces.jsonl"
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", {})
    fresh = store_module.shared_jsonl_telemetry_store(log)

    (trace,) = await fresh.list_traces()
    assert fresh.skipped_records == 0
    assert not trace.incomplete and trace.evidence_status.value == "complete"
    assert len(trace.spans) == len(before["spans"])
    assert len(trace.events) == len(before["events"])
