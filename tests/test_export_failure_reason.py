"""An exporter's failure keeps its reason in the trace.

Found writing Telemetry and exporters (D8): an export that failed ("Connection
refused") was recorded as "telemetry export failed": the recorder read the
reason from the top of the result, and the exporter puts it in `metadata`.
"""

from __future__ import annotations

from omnicoreagent.core.telemetry.exporters import TelemetryExportResult
from omnicoreagent.core.telemetry.recorder import export_failure_details


def test_the_reason_is_read_from_the_results_metadata():
    failure = TelemetryExportResult(
        exporter="otlp",
        trace_id="trace-1",
        destination=None,
        metadata={"error": "Connection refused", "error_type": "ConnectError"},
    )

    assert export_failure_details(failure) == ("ConnectError", "Connection refused")


def test_a_plain_error_still_reads():
    assert export_failure_details({"error": "boom", "error_type": "E"}) == ("E", "boom")
    assert export_failure_details(RuntimeError("x"))[1] == "x"
