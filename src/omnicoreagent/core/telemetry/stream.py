from __future__ import annotations

from collections.abc import AsyncIterator

from omnicoreagent.core.telemetry.models import TelemetryEvent, TelemetryStreamScope
from omnicoreagent.core.telemetry.store import AbstractTelemetryStore


class TelemetryStream:
    def __init__(self, store: AbstractTelemetryStore) -> None:
        self.store = store

    async def get_stream_cursor(self, scope: TelemetryStreamScope) -> str | None:
        return await self.store.get_stream_cursor(scope)

    async def stream_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> AsyncIterator[TelemetryEvent]:
        async for event in self.store.stream_after(scope, cursor):
            yield event

    async def get_events_after(
        self,
        scope: TelemetryStreamScope,
        cursor: str | None,
    ) -> list[TelemetryEvent]:
        return await self.store.get_events_after(scope, cursor)
