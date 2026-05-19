from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = ROOT / "cookbook" / "getting_started" / "agent_with_events.py"
GUARDRAILS_EXAMPLE_PATH = (
    ROOT / "cookbook" / "getting_started" / "agent_with_guardrails.py"
)


def load_events_example_module():
    script_dir = str(EXAMPLE_PATH.parent)
    sys.modules.pop("_bootstrap", None)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("agent_with_events", EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_guardrails_example_module():
    script_dir = str(GUARDRAILS_EXAMPLE_PATH.parent)
    sys.modules.pop("_bootstrap", None)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(
        "agent_with_guardrails", GUARDRAILS_EXAMPLE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeTelemetryAgent:
    async def get_telemetry_events_after(self, *, cursor, session_id, run_id):
        assert cursor is None
        assert session_id == "session-cookbook"
        assert run_id == "run-cookbook"
        return [
            SimpleNamespace(event_type="user_message", trace_id="trace-cookbook"),
            SimpleNamespace(event_type="final_answer", trace_id="trace-cookbook"),
        ]

    async def get_trace(self, trace_id=None, *, run_id=None, normalize=False):
        assert run_id is None
        assert trace_id == "trace-cookbook"
        return {
            "trace_id": trace_id,
            "run_id": "run-cookbook",
            "session_id": "session-cookbook",
            "status": "completed",
            "events": [{"event_type": "user_message"}],
            "spans": [{"kind": "agent.run"}],
            "normalized": normalize,
        }

    async def get_latest_trace(self, session_id):
        assert session_id == "session-cookbook"
        return {
            "trace_id": "trace-cookbook",
            "run_id": "run-cookbook",
            "session_id": session_id,
            "status": "completed",
            "events": [],
            "spans": [],
        }


@pytest.mark.asyncio
async def test_getting_started_events_example_collects_public_trace_surfaces():
    module = load_events_example_module()
    result = {
        "session_id": "session-cookbook",
        "run_id": "run-cookbook",
        "trace_id": "trace-cookbook",
    }

    visibility = await module.collect_run_visibility(FakeTelemetryAgent(), result)

    assert [event.event_type for event in visibility["events"]] == [
        "user_message",
        "final_answer",
    ]
    assert visibility["exact_trace"]["trace_id"] == "trace-cookbook"
    assert visibility["latest_session_trace"]["trace_id"] == "trace-cookbook"
    assert visibility["normalized_trace"]["normalized"] is True


def test_getting_started_guardrails_example_handles_blocked_result_shape():
    module = load_guardrails_example_module()
    blocked = {
        "response": "blocked",
        "guardrail_result": {
            "threat_level": "dangerous",
            "flags": ["prompt_extraction", "dense_keywords"],
        },
    }

    assert module.response_text(blocked) == "blocked"
    assert blocked["guardrail_result"]["threat_level"] == "dangerous"
    assert ", ".join(blocked["guardrail_result"]["flags"]) == (
        "prompt_extraction, dense_keywords"
    )
