"""A tool's parameter named like a secret is not a secret (X5, 2026-09-25).

A tool with a parameter called `api_key` had that parameter's JSON Schema
(`{"type": "string"}`) replaced by `[REDACTED]` in the `context_tools`
event, which marked every such run's evidence `partial`. In a schema's
`properties`, a key names a parameter; there is no value to hide.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.telemetry.redaction import REDACTION_MARKER, _redact
from test_telemetry_tool_record import ScriptedModel, _agent

KEYS = ("api_key", "token", "password", "secret")


def test_a_schema_keeps_a_parameter_named_like_a_secret():
    schema = {"type": "object", "properties": {"api_key": {"type": "string"}, "key": {"type": "string"}}}
    assert _redact(schema, KEYS) == schema


def test_a_value_under_a_secret_key_is_still_redacted():
    payload = {"api_key": "sk-live-123", "nested": {"properties": {"password": "hunter2"}}}
    redacted = _redact(payload, KEYS)
    assert redacted["api_key"] == REDACTION_MARKER
    # `properties` without a schema's "type": "object" is data, not a schema.
    assert redacted["nested"]["properties"]["password"] == REDACTION_MARKER


@pytest.mark.asyncio
async def test_a_run_with_such_a_tool_has_complete_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = await _agent(ScriptedModel(("c1", "lookup", '{"key": "a"}')), telemetry_config={"capture": "full"})
    result = await agent.run("go")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    await agent.cleanup()
    assert getattr(trace.evidence_status, "value", trace.evidence_status) == "complete"
