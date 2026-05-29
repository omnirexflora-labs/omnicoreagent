from __future__ import annotations

from typing import Any

from omnicoreagent.core.telemetry import ActorType, TelemetryActor, TelemetryError


async def emit_sandbox_event(
    recorder: Any,
    event_type: str,
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    error: BaseException | None = None,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    try:
        await recorder.emit_event(
            event_type,
            actor=TelemetryActor(type=ActorType.SYSTEM, name="sandbox"),
            input=input,
            output=output,
            error=TelemetryError.from_exception(error) if error is not None else None,
        )
    except RuntimeError:
        if strict or getattr(recorder.config, "strict", False):
            raise
        return
    except Exception:
        if strict or getattr(recorder.config, "strict", False):
            raise
