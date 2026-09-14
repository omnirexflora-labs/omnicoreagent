import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from omnicoreagent.core.runtime.construction import default_telemetry_payload_store
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    LocalTelemetryPayloadStore,
    TelemetryConfig,
    TelemetryPayloadError,
    TelemetryRecorder,
    redact_payload,
)
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent


def _checksum(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_local_payload_store_round_trips_content_addressed_payload(tmp_path):
    store = LocalTelemetryPayloadStore(tmp_path / "payloads")
    payload = {"message": "large result", "nested": [1, 2, 3]}
    checksum = _checksum(payload)

    reference = store.write(payload, checksum=checksum)

    assert reference == f"telemetry://payload/{checksum}"
    assert store.read(reference) == payload
    assert (tmp_path / "payloads" / f"{checksum}.json").exists()


def test_redaction_writes_only_redacted_payload(tmp_path):
    store = LocalTelemetryPayloadStore(tmp_path / "payloads")
    config = TelemetryConfig(
        offload_large_payloads=True,
        max_payload_bytes=32,
    )

    result = redact_payload(
        {"api_key": "secret", "body": "x" * 200},
        config,
        payload_store=store,
    )

    assert result["offloaded"] is True
    stored = store.read(result["reference"])
    assert stored["api_key"] == "[REDACTED]"
    assert stored["body"] == "x" * 200
    assert "secret" not in json.dumps(stored)


def test_offload_without_store_is_explicitly_unavailable():
    with pytest.raises(TelemetryPayloadError, match="no payload store"):
        redact_payload(
            {"body": "x" * 200},
            TelemetryConfig(offload_large_payloads=True, max_payload_bytes=32),
        )


@pytest.mark.asyncio
async def test_recorder_marks_trace_incomplete_when_payload_write_fails():
    class BrokenPayloadStore:
        def write(self, payload, *, checksum, content_type="application/json"):
            raise OSError("payload disk unavailable")

    config = TelemetryConfig(
        offload_large_payloads=True,
        max_payload_bytes=32,
        strict=False,
    )
    recorder = TelemetryRecorder(
        InMemoryTelemetryStore(),
        config=config,
        payload_store=BrokenPayloadStore(),
    )

    await recorder.start_trace(
        trace_id="trace-payload-failure",
        input={"body": "x" * 200},
    )
    await recorder.end_trace()

    trace = await recorder.store.get_trace("trace-payload-failure")
    assert trace is not None
    assert trace.incomplete is True
    assert trace.spans[0].input["truncated"] is True


@pytest.mark.asyncio
async def test_recorder_strict_payload_write_failure_raises():
    class BrokenPayloadStore:
        def write(self, payload, *, checksum, content_type="application/json"):
            raise OSError("payload disk unavailable")

    recorder = TelemetryRecorder(
        InMemoryTelemetryStore(),
        config=TelemetryConfig(
            offload_large_payloads=True,
            max_payload_bytes=32,
            strict=True,
        ),
        payload_store=BrokenPayloadStore(),
    )

    with pytest.raises(OSError, match="payload disk unavailable"):
        await recorder.start_trace(input={"body": "x" * 200})


def test_payload_store_prune_honors_retention_and_live_reference(tmp_path):
    store = LocalTelemetryPayloadStore(tmp_path / "payloads", retention_days=1)
    retained_payload = {"id": "retained"}
    expired_payload = {"id": "expired"}
    retained_ref = store.write(
        retained_payload,
        checksum=_checksum(retained_payload),
    )
    expired_ref = store.write(
        expired_payload,
        checksum=_checksum(expired_payload),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(tmp_path / "payloads" / f"{_checksum(expired_payload)}.json", (old_timestamp, old_timestamp))

    assert store.prune(references={retained_ref}) == 1
    assert store.read(retained_ref) == retained_payload
    with pytest.raises(FileNotFoundError):
        store.read(expired_ref)


def test_default_payload_store_uses_explicit_jsonl_location(tmp_path):
    path = tmp_path / "telemetry" / "traces.jsonl"
    store = default_telemetry_payload_store(
        telemetry_config={
            "storage": "jsonl",
            "storage_path": str(path),
            "offload_large_payloads": True,
        }
    )

    assert isinstance(store, LocalTelemetryPayloadStore)
    reference = store.write({"value": "ok"}, checksum=_checksum({"value": "ok"}))
    assert store.read(reference) == {"value": "ok"}


def test_agent_wires_payload_store_from_telemetry_config(tmp_path):
    agent = OmniCoreAgent(
        name="payload-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.6-mini"},
        telemetry_config={
            "storage": "jsonl",
            "storage_path": str(tmp_path / "traces.jsonl"),
            "offload_large_payloads": True,
        },
    )

    agent._ensure_telemetry()

    assert isinstance(agent.telemetry_payload_store, LocalTelemetryPayloadStore)
    assert agent.telemetry_recorder.payload_store is agent.telemetry_payload_store


@pytest.mark.asyncio
async def test_agent_reads_and_prunes_telemetry_payloads(tmp_path):
    path = tmp_path / "traces.jsonl"
    agent = OmniCoreAgent(
        name="payload-api-agent",
        system_instruction="You are a test agent.",
        model_config={"provider": "openai", "model": "gpt-5.6-mini"},
        telemetry_config={
            "storage": "jsonl",
            "storage_path": str(path),
            "offload_large_payloads": True,
        },
    )
    agent._ensure_telemetry()
    payload = {"message": "large"}
    reference = agent.telemetry_payload_store.write(
        payload,
        checksum=_checksum(payload),
    )

    assert await agent.read_telemetry_payload(reference) == payload
    assert await agent.prune_telemetry_payloads(retention_days=None) == 0
