"""Credential-free acceptance fixture for the portable telemetry boundary.

The fixture has two purposes:

* ``--write-fixtures`` runs the native agent loop with a deterministic model,
  persists JSONL telemetry, exports the portable contract, and writes a
  sanitized bundle for independent review.
* ``--check-fixture`` validates the committed bundle without importing the
  runtime's telemetry models or making a model call.  This is the review path.

The model and tools below are deliberately synthetic.  They exercise the
runtime boundaries that a production model uses while keeping credentials and
external services out of the acceptance evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures" / "telemetry-evidence-acceptance"
CASE_NAMES = ("transformed", "failure", "capture-restricted")
QUALITY_REQUIREMENT = (
    "The final answer must report ACCEPTANCE_READY only after reading the "
    "marker from the large tool result artifact."
)


class AcceptanceModel:
    """Deterministic provider substitute that records every model request."""

    def __init__(self, case: str):
        self.case = case
        self.calls: list[list[Any]] = []

    async def llm_call(self, messages, tools=None):
        from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest

        self.calls.append(list(messages))
        transcript = "\n".join(
            str(getattr(message, "content", message)) for message in messages
        )
        if self.case == "transformed":
            if len(self.calls) == 1:
                return ModelTurn(
                    tool_calls=(
                        ToolRequest(
                            "call_bulk_report",
                            "bulk_report",
                            "{}",
                        ),
                    ),
                    finish_reason="tool_calls",
                )
            if len(self.calls) == 2:
                match = re.search(r"Artifact ID: ([A-Za-z0-9_]+)", transcript)
                if not match:
                    raise AssertionError("offload observation did not reach model")
                return ModelTurn(
                    tool_calls=(
                        ToolRequest(
                            "call_read_artifact",
                            "read_artifact",
                            json.dumps({"artifact_id": match.group(1)}),
                        ),
                    ),
                    finish_reason="tool_calls",
                )
            return "ACCEPTANCE_READY"
        if self.case == "failure":
            if len(self.calls) == 1:
                return ModelTurn(
                    tool_calls=(
                        ToolRequest(
                            "call_failing_tool",
                            "failing_tool",
                            "{}",
                        ),
                    ),
                    finish_reason="tool_calls",
                )
            return "FAILURE_ACKNOWLEDGED"
        return "CAPTURE_RESTRICTION_READY"


def _no_redaction() -> Any:
    from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter

    return PrivacyFilter(
        PrivacyConfig(
            redact_telemetry=False,
            redact_memory=False,
            redact_workspace=False,
            redact_stream=False,
            redact_public=False,
            redact_model_io=False,
        )
    )


def _history_callbacks(history: list[dict[str, Any]]):
    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(session_id, agent_name=None):
        return [
            item
            for item in history
            if item["session_id"] == session_id
            and (agent_name is None or item["metadata"].get("agent_name") == agent_name)
        ]

    return add_message_to_history, message_history


async def _run_case(case: str, root: Path) -> dict[str, Any]:
    from omnicoreagent.core.agents.base import BaseReactAgent
    from omnicoreagent.core.telemetry import (
        ActorType,
        JsonlTelemetryStore,
        OmniCoreEvidenceAdapter,
        TelemetryActor,
        TelemetryConfig,
        TelemetryRecorder,
        TraceStatus,
        validate_portable_evidence_document,
    )
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
    from omnicoreagent.core.privacy import PrivacyFilter
    from omnicoreagent.core.workspace.config import WorkspaceConfig

    workspace_dir = root / "workspace"
    jsonl_path = root / "telemetry.jsonl"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    local_tools = ToolRegistry()

    @local_tools.register_tool("bulk_report", description="Return a large report.")
    def bulk_report() -> dict[str, Any]:
        return {
            "marker": "TELEMETRY_ACCEPTANCE_MARKER",
            "lines": [f"synthetic evidence line {index}" for index in range(180)],
        }

    if case == "failure":

        @local_tools.register_tool("failing_tool", description="Always fails.")
        def failing_tool() -> dict[str, Any]:
            raise RuntimeError("synthetic tool failure for acceptance")

    model = AcceptanceModel(case)
    config = TelemetryConfig(
        storage="jsonl",
        storage_path=str(jsonl_path),
        record_model_prompts=case != "capture-restricted",
        record_model_responses=case != "capture-restricted",
        record_tool_results=case != "capture-restricted",
        max_payload_bytes=200_000,
    )
    privacy = _no_redaction() if case != "capture-restricted" else PrivacyFilter()
    store = JsonlTelemetryStore(jsonl_path)
    recorder = TelemetryRecorder(store, config=config, privacy_filter=privacy)
    trace_context = await recorder.start_trace(
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="telemetry_acceptance"),
        trace_id=f"trace_acceptance_{case}",
        run_id=f"run_acceptance_{case}",
        session_id=f"session_acceptance_{case}",
        agent_id="telemetry_acceptance",
        input={"query": "Verify the telemetry evidence path."},
    )
    await recorder.emit_event(
        "user_message",
        actor=TelemetryActor(type=ActorType.USER),
        input={"message": "Verify the telemetry evidence path."},
    )
    agent = BaseReactAgent(
        agent_name="telemetry_acceptance",
        max_steps=8,
        tool_call_timeout=5,
        enable_workspace_files=True,
        tool_offload_config={
            "enabled": case == "transformed",
            "threshold_tokens": 20,
            "threshold_bytes": 200,
            "max_preview_tokens": 20,
            "max_preview_lines": 8,
        },
        workspace_config=WorkspaceConfig(workspace_dir=workspace_dir),
        privacy_filter=privacy,
    )
    add_history, message_history = _history_callbacks(history)
    result = await agent.run(
        system_prompt="You are a telemetry acceptance agent.",
        query="Verify the telemetry evidence path.",
        llm_connection=model,
        add_message_to_history=add_history,
        message_history=message_history,
        local_tools=local_tools,
        session_id=f"session_acceptance_{case}",
        telemetry_recorder=recorder,
    )
    await recorder.emit_event(
        "final_answer",
        actor=TelemetryActor(type=ActorType.AGENT, name="telemetry_acceptance"),
        output={
            "response": result.get("answer"),
            "status": result.get("status", "success"),
            "termination_reason": result.get("termination_reason"),
        },
    )
    await recorder.end_trace(
        status=(
            TraceStatus.COMPLETED
            if result.get("status", "success") == "success"
            else TraceStatus.FAILED
        ),
        output={"response": result.get("answer")},
    )
    persisted_store = JsonlTelemetryStore(jsonl_path)
    persisted_trace = await persisted_store.get_trace(trace_context.trace_id)
    if persisted_trace is None:
        raise AssertionError(f"no persisted trace for {case}")
    evidence = OmniCoreEvidenceAdapter().import_trace(
        persisted_trace,
        task={"requirement": QUALITY_REQUIREMENT},
    )
    document = evidence.model_dump()
    validate_portable_evidence_document(document)
    round_trip = OmniCoreEvidenceAdapter().import_document(
        json.loads(json.dumps(document))
    )
    if round_trip.model_dump() != document:
        raise AssertionError(f"portable round trip changed {case} evidence")
    if case == "transformed":
        artifact_files = list((workspace_dir / "artifacts").glob("bulk_report_*.json"))
        if not artifact_files:
            raise AssertionError("transformed fixture did not persist its artifact")
        artifact_target = root / "artifact.json"
        shutil.copyfile(artifact_files[0], artifact_target)
    return document


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_strings(item, replacements) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, str):
        result = value
        # Replace longer values first so a short identifier cannot modify a
        # longer path before the path-specific replacement is applied.
        for old, new in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            result = result.replace(old, new)
        return result
    return value


def _sanitization_replacements(document: dict[str, Any], case: str) -> dict[str, str]:
    """Make identifiers stable while retaining the complete trace payload."""
    trace = document["trace"]
    replacements: dict[str, str] = {}
    for key in ("trace_id", "run_id", "session_id"):
        value = trace.get(key)
        if isinstance(value, str):
            replacements[value] = f"{key}_{case}"
    for index, span in enumerate(trace.get("spans", [])):
        value = span.get("span_id")
        if isinstance(value, str):
            replacements[value] = f"span_{case}_{index:03d}"
    for index, event in enumerate(trace.get("events", [])):
        value = event.get("event_id")
        if isinstance(value, str):
            replacements[value] = f"event_{case}_{index:03d}"
    if case == "transformed":
        for event in trace.get("events", []):
            output = event.get("output")
            if not isinstance(output, dict):
                continue
            reference = output.get("reference")
            if isinstance(reference, str):
                path_match = re.search(r"Full response saved to: ([^\n]+)", reference)
                if path_match:
                    replacements[path_match.group(1)] = "artifacts/bulk_report.json"
                match = re.search(r"Artifact ID: ([A-Za-z0-9_]+)", reference)
                if match:
                    replacements[match.group(1)] = "artifact_bulk_report"
    return replacements


def _sanitize(document: dict[str, Any], case: str) -> dict[str, Any]:
    return _replace_strings(document, _sanitization_replacements(document, case))


def _string_replacements(before: Any, after: Any) -> dict[str, str]:
    """Collect exact string substitutions between a raw and sanitized value."""
    replacements: dict[str, str] = {}
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() & after.keys():
            replacements.update(_string_replacements(before[key], after[key]))
    elif isinstance(before, list) and isinstance(after, list):
        for raw_item, safe_item in zip(before, after):
            replacements.update(_string_replacements(raw_item, safe_item))
    elif isinstance(before, str) and isinstance(after, str) and before != after:
        replacements[before] = after
    return replacements


def _event(trace: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [event for event in trace["events"] if event.get("event_type") == event_type]


def _validate_portable_document(document: dict[str, Any]) -> None:
    """Minimal standalone validation used by the review-only command.

    This intentionally uses only JSON and the published envelope rules.  A
    reviewer can run ``--check-fixture`` without installing OmniCoreAgent.
    """
    if not isinstance(document, dict):
        raise AssertionError("portable evidence must be an object")
    if document.get("contract") != "omnicoreagent.execution-evidence/v1":
        raise AssertionError("unexpected evidence contract")
    if document.get("schema_version") != 1:
        raise AssertionError("unexpected evidence schema version")
    for key in ("execution_id", "source", "adapter"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise AssertionError(f"missing portable envelope field: {key}")
    trace = document.get("trace")
    if not isinstance(trace, dict):
        raise AssertionError("portable evidence trace must be an object")
    trace_id = trace.get("trace_id")
    root_span_id = trace.get("root_span_id")
    spans = trace.get("spans")
    events = trace.get("events")
    if not isinstance(trace_id, str) or not trace_id:
        raise AssertionError("trace_id is required")
    if not isinstance(root_span_id, str) or not root_span_id:
        raise AssertionError("root_span_id is required")
    if not isinstance(spans, list) or not isinstance(events, list):
        raise AssertionError("trace spans/events arrays are required")
    span_ids = {span.get("span_id") for span in spans if isinstance(span, dict)}
    event_ids = {event.get("event_id") for event in events if isinstance(event, dict)}
    if len(span_ids) != len(spans) or None in span_ids:
        raise AssertionError("span IDs must be unique and nonempty")
    if len(event_ids) != len(events) or None in event_ids:
        raise AssertionError("event IDs must be unique and nonempty")
    if root_span_id not in span_ids:
        raise AssertionError("root span is absent")
    for span in spans:
        if span.get("trace_id") != trace_id:
            raise AssertionError("span trace relationship is invalid")
        for event_id in span.get("event_ids") or []:
            if event_id not in event_ids:
                raise AssertionError("span references an unknown event")
    for event in events:
        if event.get("trace_id") != trace_id:
            raise AssertionError("event trace relationship is invalid")
        span_id = event.get("span_id")
        if span_id is not None and span_id not in span_ids:
            raise AssertionError("event references an unknown span")


def _assert_capture(document: dict[str, Any], case: str) -> None:
    _validate_portable_document(document)
    trace = document["trace"]
    events = trace["events"]
    types = [event.get("event_type") for event in events]
    assert trace["status"] == "completed", (case, trace["status"])
    assert _event(trace, "user_message"), case
    assert _event(trace, "final_answer"), case
    if case == "transformed":
        for required in (
            "context_assembly",
            "model_call",
            "model_response",
            "tool_requested",
            "tool_resolved",
            "tool_result",
            "workspace_offload",
            "tool_observation",
            "observation_pipeline_end",
            "workspace_read",
        ):
            assert _event(trace, required), (required, types)
        result_event = next(
            event
            for event in events
            if event.get("event_type") == "tool_result"
            and event.get("output", {}).get("tool_name") == "bulk_report"
        )
        offload_event = _event(trace, "workspace_offload")[0]
        observation_event = next(
            event
            for event in events
            if event.get("event_type") == "tool_observation"
            and event.get("metadata", {}).get("observation_for")
            == result_event.get("metadata", {}).get("tool_call_id")
        )
        call_id = result_event["metadata"]["tool_call_id"]
        assert offload_event["metadata"]["tool_call_id"] == call_id
        assert observation_event["metadata"]["tool_call_id"] == call_id
        assert observation_event["metadata"]["observation_for"] == call_id
        result_bytes = result_event["output_capture"]["original_bytes"]
        observation_content = observation_event["output"]["message"]["content"]
        observation_bytes = observation_event["output_capture"]["original_bytes"]
        assert result_bytes > observation_bytes
        assert "[TOOL RESPONSE OFFLOADED]" in observation_content
        assert "TELEMETRY_ACCEPTANCE_MARKER" in json.dumps(result_event["output"])
        assert any(
            "TELEMETRY_ACCEPTANCE_MARKER" in json.dumps(event.get("output"))
            for event in _event(trace, "workspace_read")
        ), "artifact readback did not contain the marker"
        context_events = _event(trace, "context_assembly")
        assert any(
            "[TOOL RESPONSE OFFLOADED]" in json.dumps(event.get("input"))
            for event in context_events
        ), "follow-up model context did not retain the observation"
        final = _event(trace, "final_answer")[-1]
        assert final["output"]["response"] == "ACCEPTANCE_READY"
        positions = {
            kind: types.index(kind)
            for kind in (
                "tool_requested",
                "tool_resolved",
                "tool_result",
                "workspace_offload",
                "tool_observation",
            )
        }
        assert (
            positions["tool_requested"]
            < positions["tool_resolved"]
            < positions["tool_result"]
            < positions["workspace_offload"]
            < positions["tool_observation"]
        )
        assert document["missing_evidence"] == [], document["missing_evidence"]
    elif case == "failure":
        errors = [event for event in events if event.get("event_type") == "tool_error"]
        assert errors, types
        assert any("synthetic tool failure" in json.dumps(event) for event in errors)
        assert (
            _event(trace, "final_answer")[-1]["output"]["response"]
            == "FAILURE_ACKNOWLEDGED"
        )
    else:
        assert trace["evidence_status"] == "partial"
        assert document["missing_evidence"]
        assert any(
            item.get("state") == "not_recorded" for item in document["missing_evidence"]
        )
        assert (
            _event(trace, "final_answer")[-1]["output"]["response"]
            == "CAPTURE_RESTRICTION_READY"
        )


def check_fixture() -> None:
    for case in CASE_NAMES:
        path = FIXTURE_DIR / f"{case}.json"
        if not path.exists():
            raise SystemExit(f"missing committed fixture: {path}")
        _assert_capture(json.loads(path.read_text()), case)
        persisted = FIXTURE_DIR / f"{case}.jsonl"
        if not persisted.exists():
            raise SystemExit(f"missing persisted JSONL fixture: {persisted}")
        lines = [line for line in persisted.read_text().splitlines() if line.strip()]
        if not lines:
            raise SystemExit(f"persisted JSONL fixture is empty: {persisted}")
        for line in lines:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(
                record.get("record_type"), str
            ):
                raise SystemExit(f"invalid JSONL record in {persisted}")
        if "/tmp/" in persisted.read_text():
            raise SystemExit(f"unsanitized temporary path in {persisted}")
    artifact = FIXTURE_DIR / "artifacts" / "bulk_report.json"
    if (
        not artifact.exists()
        or "TELEMETRY_ACCEPTANCE_MARKER" not in artifact.read_text()
    ):
        raise SystemExit("transformed fixture artifact is missing or incomplete")
    print(f"checked {len(CASE_NAMES)} portable traces and 1 referenced artifact")


async def write_fixtures() -> None:
    with tempfile.TemporaryDirectory(prefix="omni-telemetry-acceptance-") as temporary:
        root = Path(temporary)
        generated: dict[str, dict[str, Any]] = {}
        for case in CASE_NAMES:
            raw_document = await _run_case(case, root / case)
            generated[case] = _sanitize(raw_document, case)
            raw_jsonl = root.joinpath(case, "telemetry.jsonl").read_text()
            replacements = _string_replacements(raw_document, generated[case])
            sanitized_jsonl = (
                "\n".join(
                    json.dumps(
                        _replace_strings(json.loads(line), replacements), sort_keys=True
                    )
                    for line in raw_jsonl.splitlines()
                    if line.strip()
                )
                + "\n"
            )
            (FIXTURE_DIR / f"{case}.jsonl").parent.mkdir(parents=True, exist_ok=True)
            # The target is created below; keep the sanitized persistence text
            # in memory until the fixture directory is ready.
            generated[f"{case}.__jsonl"] = sanitized_jsonl
        target = FIXTURE_DIR
        target.mkdir(parents=True, exist_ok=True)
        (target / "artifacts").mkdir(parents=True, exist_ok=True)
        for case in CASE_NAMES:
            document = generated[case]
            (target / f"{case}.json").write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n"
            )
            (target / f"{case}.jsonl").write_text(generated[f"{case}.__jsonl"])
        shutil.copyfile(
            root / "transformed" / "artifact.json",
            target / "artifacts" / "bulk_report.json",
        )
    check_fixture()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-fixtures", action="store_true")
    parser.add_argument("--check-fixture", action="store_true")
    args = parser.parse_args()
    if args.write_fixtures:
        asyncio.run(write_fixtures())
        return
    check_fixture()


if __name__ == "__main__":
    main()
