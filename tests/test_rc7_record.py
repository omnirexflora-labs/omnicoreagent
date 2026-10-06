"""The record under the rc7 gate's area E findings.

E7-2: the command text follows the argument capture policy in the trace.
E7-3: the normalizer's notes are their own event types, not runtime errors.
"""

from __future__ import annotations

from omnicoreagent.core.telemetry import InMemoryTelemetryStore, TelemetryRecorder
from omnicoreagent.core.telemetry.redaction import TelemetryConfig


def _recorder(capture: str) -> TelemetryRecorder:
    return TelemetryRecorder(InMemoryTelemetryStore(), config=TelemetryConfig.from_value({"capture": capture}))


def test_the_approval_summary_follows_the_capture_policy_in_the_trace():
    from omnicoreagent.governance.models import ApprovalRequest
    from omnicoreagent.governance.telemetry import _recorded_approval

    request = ApprovalRequest(
        request_id="authreq_1", decision_id="decision_1", capability="process.exec", actor="agent",
        metadata={"command": {"summary": ["printf token=secret-xyz > note.txt", "cat note.txt"], "opaque": True}},
    )
    kept = _recorded_approval(_recorder("full"), request)
    # Full capture keeps the text and redacts a credential in it, as a
    # sandbox command's record does.
    assert kept["metadata"]["command"]["summary"] == ["printf token=[REDACTED] > note.txt", "cat note.txt"]
    assert "secret-xyz" not in str(kept)
    private = _recorded_approval(_recorder("default"), request)
    assert private["metadata"]["command"]["summary"] == ["[REDACTED] (2 command(s))"]
    assert "secret-xyz" not in str(private)


def test_a_sandbox_commands_text_follows_the_capture_policy_in_the_trace():
    from types import SimpleNamespace

    from omnicoreagent.sandbox.execution import SandboxExecutionService

    def service(capture):
        return SandboxExecutionService(SimpleNamespace(telemetry_recorder=_recorder(capture)))

    command = ["sh", "-c", "printf token=secret-xyz > note.txt"]
    # Under full capture the text is kept, with a credential in it redacted as
    # it is in free text (0.5.1: the start event carries it too).
    assert service("full")._recorded(command) == ["sh", "-c", "printf token=[REDACTED] > note.txt"]
    assert service("full")._recorded(["ls", "-l"]) == ["ls", "-l"]
    assert service("default")._recorded(command) == ["sh", "[REDACTED] (2 argument(s))"]


def test_the_normalizers_notes_are_not_runtime_errors():
    from omnicoreagent.core.telemetry.models import FOUNDATION_EVENT_TYPES
    from omnicoreagent.core.telemetry import normalizer

    assert {"capture_gaps", "missing_evidence"} <= FOUNDATION_EVENT_TYPES
    source = open(normalizer.__file__).read()
    assert 'event_type="runtime_error"' not in source
