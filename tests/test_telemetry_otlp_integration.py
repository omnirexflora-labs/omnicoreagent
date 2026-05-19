from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    OTLPHttpTelemetryExporter,
    SpanStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
)


pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")


class _OtlpReceiver(BaseHTTPRequestHandler):
    requests: list[dict] = []
    status_code = 200

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append(
            {
                "path": self.path,
                "body": body,
                "content_type": self.headers.get("content-type"),
            }
        )
        self.send_response(self.__class__.status_code)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _trace() -> TelemetryTrace:
    now = datetime.now(timezone.utc)
    root = TelemetrySpan(
        trace_id="trace-live-otlp",
        span_id="span-root",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="assistant"),
        status=SpanStatus.OK,
        started_at=now,
        ended_at=now,
    )
    return TelemetryTrace(
        trace_id="trace-live-otlp",
        root_span_id="span-root",
        status=TraceStatus.COMPLETED,
        started_at=now,
        ended_at=now,
        run_id="run-live-otlp",
        session_id="session-live-otlp",
        spans=[root],
        events=[
            TelemetryEvent(
                trace_id="trace-live-otlp",
                span_id="span-root",
                event_type="final_answer",
                actor=TelemetryActor(type=ActorType.AGENT, name="assistant"),
                output={"response": "done"},
            )
        ],
    )


@pytest.mark.asyncio
async def test_otlp_http_exporter_sends_trace_to_local_receiver():
    _OtlpReceiver.requests.clear()
    _OtlpReceiver.status_code = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OtlpReceiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
        exporter = OTLPHttpTelemetryExporter(endpoint=endpoint, timeout=2)

        result = await exporter.export_trace(_trace())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.exporter == "otlp"
    assert result.exported_spans == 1
    assert result.exported_events == 1
    assert len(_OtlpReceiver.requests) == 1
    assert _OtlpReceiver.requests[0]["path"] == "/v1/traces"
    assert _OtlpReceiver.requests[0]["body"]


@pytest.mark.asyncio
async def test_otlp_http_exporter_raises_when_receiver_rejects_trace():
    from omnicoreagent.core.telemetry import TelemetryExportError

    _OtlpReceiver.requests.clear()
    _OtlpReceiver.status_code = 500
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OtlpReceiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
        exporter = OTLPHttpTelemetryExporter(endpoint=endpoint, timeout=2)

        with pytest.raises(TelemetryExportError, match="status 500"):
            await exporter.export_trace(_trace())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert len(_OtlpReceiver.requests) == 1
