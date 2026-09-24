"""An archive that cannot be written to must say so.

Found while bringing up two server processes on one archive (scale plan S4):
the shared directory for trace bodies was not writable by the processes, and
nothing said anything. Every finished trace stayed in each process's own log,
each process answered only for its own runs, and the deployment looked healthy
— the failing write was swallowed, because telemetry does not fail a run.

Not failing the run is right. Saying nothing is not. So: the failure is
counted and logged, the trace stays readable and stays in the log where
nothing is lost, and the next flush tries again — a deployment that fixes the
permission gets its traces archived without restarting.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from omnicoreagent.core.telemetry.archive import TelemetryArchive
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
    utc_now,
)
from omnicoreagent.core.telemetry.store import JsonlTelemetryStore


class RefusingBodies:
    """Workspace storage that will not take a write, as an unwritable directory."""

    def __init__(self) -> None:
        self.allow = False
        self.written: dict[str, str] = {}

    def write_text(self, name: str, text: str) -> None:
        if not self.allow:
            raise PermissionError(f"cannot write {name}")
        self.written[name] = text

    def read_text(self, name: str) -> str:
        return self.written[name]

    def delete(self, name: str) -> None:
        self.written.pop(name, None)

    def exists(self, name: str) -> bool:
        return name in self.written


async def _finished_trace(store: JsonlTelemetryStore, number: int) -> str:
    trace_id = f"trace_{number:032x}"
    span = TelemetrySpan(
        trace_id=trace_id,
        span_id=f"span_{number:032x}",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="agent"),
        started_at=utc_now(),
    )
    await store.upsert_trace(
        TelemetryTrace(
            trace_id=trace_id,
            root_span_id=span.span_id,
            run_id=f"run-{number}",
            session_id="session",
            agent_id="agent",
            status=TraceStatus.RUNNING,
            started_at=utc_now(),
            spans=[span],
        )
    )
    await store.append_event(
        trace_id,
        TelemetryEvent(
            trace_id=trace_id,
            event_type="user_message",
            actor=TelemetryActor(type=ActorType.USER),
            span_id=span.span_id,
            event_id=f"event_{number:032x}",
            sequence_number=1,
            input={"message": "question"},
        ),
    )
    await store.upsert_trace(
        replace(
            await store.get_trace(trace_id),
            status=TraceStatus.COMPLETED,
            ended_at=utc_now(),
        )
    )
    return trace_id


@pytest.mark.asyncio
async def test_an_archive_that_refuses_a_write_is_counted_and_retried(tmp_path, caplog):
    bodies = RefusingBodies()
    store = JsonlTelemetryStore(
        tmp_path / "traces.jsonl",
        retention_days=None,
        archive=TelemetryArchive(tmp_path / "archive", bodies=bodies),
    )
    try:
        with caplog.at_level("WARNING"):
            trace_id = await _finished_trace(store, 1)
            await store.flush()

        # The run is not failed by it, and the trace is still there to read.
        kept = await store.get_trace(trace_id)
        assert kept is not None and kept.status == TraceStatus.COMPLETED
        assert store.archive_failures == 1
        assert "cannot write" in store.last_archive_error
        assert any("archive" in record.message.lower() for record in caplog.records), [
            record.message for record in caplog.records
        ]

        # Nothing was lost: the trace is still in the log, so a restart finds it.
        assert trace_id in (tmp_path / "traces.jsonl").read_text()

        # And the next flush tries again, so fixing the directory is enough.
        bodies.allow = True
        await store.flush()
        assert store.archive_failures == 1
        assert await store.archive.contains(trace_id)
        assert (await store.get_trace(trace_id)).trace_id == trace_id
    finally:
        await store.close()
