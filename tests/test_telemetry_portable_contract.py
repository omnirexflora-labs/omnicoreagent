from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import (
    EvidenceValidationError,
    GenericTraceEvidenceAdapter,
    OmniCoreEvidenceAdapter,
    validate_portable_evidence_document,
)
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_ROOT = Path(__file__).resolve().parents[1]
_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


class ScriptedModel:
    def __init__(self, *turns) -> None:
        self.turns = list(turns)

    def estimate_cost(self, usage):
        return usage.total_tokens * 1e-6

    async def llm_call(self, messages, tools=None, **kwargs):
        turn = self.turns.pop(0)
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn),
            finish_reason="tool_calls",
            usage=usage,
        )


async def _exported_run():
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key}

    agent = OmniCoreAgent(
        name="contract-agent",
        system_instruction="You are a contract probe.",
        model_config=_MODEL,
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
        # The privacy-first preset: a trace with gaps, which is what this
        # contract is about.
        telemetry_config={"capture": "default"},
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel([("call_1", "lookup", '{"key": "a"}')], "done")
    result = await agent.run("go", session_id="contract")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    return OmniCoreEvidenceAdapter().import_trace(trace).model_dump()


def _event(document, event_type):
    return next(e for e in document["trace"]["events"] if e["event_type"] == event_type)


def test_packaged_schema_matches_the_published_specification():
    from importlib.resources import files

    packaged = json.loads(
        files("omnicoreagent.core.telemetry")
        .joinpath("schemas/portable-execution-evidence.schema.json")
        .read_text()
    )
    published = json.loads(
        (_ROOT / "engineering/specifications/portable-execution-evidence.schema.json").read_text()
    )
    assert packaged == published


@pytest.mark.asyncio
async def test_current_runtime_export_validates_and_the_schema_types_new_fields():
    document = await _exported_run()
    validate_portable_evidence_document(document)

    broken = copy.deepcopy(document)
    _event(broken, "final_answer")["metadata"]["run_summary"]["tokens"]["total"] = "many"
    with pytest.raises(EvidenceValidationError, match="run_summary"):
        validate_portable_evidence_document(broken)

    broken = copy.deepcopy(document)
    _event(broken, "model_call")["metadata"]["purpose"] = "vibes"
    with pytest.raises(EvidenceValidationError, match="purpose"):
        validate_portable_evidence_document(broken)

    broken = copy.deepcopy(document)
    _event(broken, "model_response")["metadata"]["model_call"]["tokens"]["input"] = -3
    with pytest.raises(EvidenceValidationError):
        validate_portable_evidence_document(broken)


@pytest.mark.asyncio
async def test_documents_the_schema_accepts_import_cleanly():
    document = await _exported_run()
    extended = copy.deepcopy(document)
    extended["trace"]["producer_note"] = "from a newer producer"
    extended["trace"]["spans"][0]["vendor_field"] = {"x": 1}
    extended["trace"]["events"][0]["vendor_field"] = True
    extended["trace"]["spans"][-1]["kind"] = "custom.vendor.kind"
    extended["trace"]["events"][0]["actor"]["type"] = "robot"

    evidence = OmniCoreEvidenceAdapter().import_document(extended)

    assert evidence.trace["producer_note"] == "from a newer producer"
    assert evidence.trace["spans"][-1]["kind"] == "custom.vendor.kind"


@pytest.mark.asyncio
async def test_invalid_documents_raise_evidence_validation_errors_only():
    document = await _exported_run()
    cases = []
    bad_state = copy.deepcopy(document)
    bad_state["trace"]["events"][0]["input_capture"] = {
        "state": "bogus",
        "source": "user",
        "role": "request",
    }
    cases.append(bad_state)
    bad_time = copy.deepcopy(document)
    bad_time["trace"]["spans"][0]["started_at"] = "not a time"
    cases.append(bad_time)
    no_reason = copy.deepcopy(document)
    no_reason["trace"]["events"][0]["input_capture"] = {
        "state": "missing",
        "source": "user",
        "role": "request",
    }
    cases.append(no_reason)

    for case in cases:
        with pytest.raises(EvidenceValidationError):
            OmniCoreEvidenceAdapter().import_document(case)


@pytest.mark.asyncio
async def test_import_recomputes_a_claimed_complete_status():
    document = await _exported_run()
    assert document["trace"]["evidence_status"] == "partial"
    claimed = copy.deepcopy(document)
    claimed["trace"]["evidence_status"] = "complete"
    claimed["missing_evidence"] = []

    evidence = OmniCoreEvidenceAdapter().import_document(claimed)

    assert evidence.trace["evidence_status"] == "partial"
    assert {
        "type": "evidence_status_claim",
        "claimed": "complete",
        "recomputed": "partial",
    } in evidence.missing_evidence
    assert any(item.get("state") == "not_recorded" for item in evidence.missing_evidence)


@pytest.mark.asyncio
async def test_import_rejects_references_to_absent_records():
    document = await _exported_run()
    for key in ("facts", "final_output_references"):
        broken = copy.deepcopy(document)
        broken[key].append({"kind": "event", "id": "event_that_never_happened"})
        with pytest.raises(EvidenceValidationError, match="unknown"):
            OmniCoreEvidenceAdapter().import_document(broken)


def _external_trace(**overrides):
    trace = {
        "trace_id": "ext-1",
        "status": "completed",
        "evidence_status": "complete",
        "started_at": "2026-09-18T10:00:00+00:00",
        "ended_at": "2026-09-18T10:00:02+00:00",
        "schema_version": 1,
        "metadata": {"extra": {}},
        "spans": [
            {
                "id": "root",
                "kind": "agent",
                "name": "agent",
                "actor": {"type": "agent"},
                "status": "ok",
                "started_at": "2026-09-18T10:00:00+00:00",
                "ended_at": "2026-09-18T10:00:02+00:00",
                "schema_version": 1,
            }
        ],
        "events": [],
    }
    trace.update(overrides)
    return trace


def test_generic_import_never_invents_event_facts():
    raw = _external_trace(
        events=[
            {"id": "e1", "timestamp": "2026-09-18T10:00:01+00:00", "schema_version": 1},
        ]
    )
    evidence = GenericTraceEvidenceAdapter().import_trace(raw)

    [event] = evidence.trace["events"]
    assert event["event_type"] != "runtime_error"
    missing = {(item["type"], item.get("id")) for item in evidence.missing_evidence}
    assert ("event_event_type", "e1") in missing
    assert ("event_actor", "e1") in missing
    assert ("event_span_id", "e1") in missing
    assert evidence.trace["evidence_status"] == "partial"


def test_generic_import_marks_defaulted_span_kind_and_actor():
    raw = _external_trace()
    del raw["spans"][0]["kind"]
    del raw["spans"][0]["actor"]

    evidence = GenericTraceEvidenceAdapter().import_trace(raw)

    missing = {(item["type"], item.get("id")) for item in evidence.missing_evidence}
    assert ("span_kind", "root") in missing
    assert ("span_actor", "root") in missing


def test_generic_import_downgrades_a_complete_claim_with_missing_fields():
    raw = _external_trace()
    del raw["spans"][0]["ended_at"]

    evidence = GenericTraceEvidenceAdapter().import_trace(raw)

    assert evidence.trace["evidence_status"] == "partial"


def test_generic_import_rejects_negative_cost_as_missing():
    raw = _external_trace()
    raw["spans"][0]["cost_usd"] = -1.5

    evidence = GenericTraceEvidenceAdapter().import_trace(raw)
    validate_portable_evidence_document(evidence.model_dump())

    assert evidence.trace["spans"][0]["estimated_cost_usd"] is None
    missing = {(item["type"], item.get("id")) for item in evidence.missing_evidence}
    assert ("span_cost", "root") in missing


def test_generic_import_does_not_modify_its_input():
    raw = _external_trace(status="vendor_specific_state")
    snapshot = copy.deepcopy(raw)

    GenericTraceEvidenceAdapter().import_trace(raw)

    assert raw == snapshot
