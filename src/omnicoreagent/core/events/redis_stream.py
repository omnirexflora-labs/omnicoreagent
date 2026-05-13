import os

import redis.asyncio as redis
from collections.abc import AsyncIterator

from omnicoreagent.core.events.base import BaseEventStore, Event, validate_event

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

APPEND_EVENT_SCRIPT = """
local stream_name = KEYS[1]
local sequence = redis.call('XLEN', stream_name) + 1
local entry_id = redis.call(
    'XADD',
    stream_name,
    '*',
    'event',
    ARGV[1],
    'sequence',
    tostring(sequence)
)
return {entry_id, tostring(sequence)}
"""


class RedisStreamEventStore(BaseEventStore):
    """
    Redis Streams event store.

    Key design: stream() captures an explicit Redis stream cursor, then reads
    entries after that cursor. Historical events are available via get_events().
    """

    def __init__(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)

    async def append(self, session_id: str, event: Event):
        """Append event to Redis stream."""
        validate_event(event)
        stream_name = f"omnicoreagent_events:{session_id}"
        _, sequence = await self.redis.eval(
            APPEND_EVENT_SCRIPT,
            1,
            stream_name,
            event.json(),
        )
        event.sequence = int(sequence)

    async def get_events(self, session_id: str) -> list[Event]:
        """Get all historical events for a session."""
        stream_name = f"omnicoreagent_events:{session_id}"
        events = await self.redis.xrange(stream_name, min="-", max="+")
        return [_event_from_redis_entry(entry[1]) for entry in events]

    async def get_events_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> list[Event]:
        """Get historical events appended after the Redis stream cursor."""
        stream_name = f"omnicoreagent_events:{session_id}"
        min_id = f"({cursor or '0-0'}"
        events = await self.redis.xrange(stream_name, min=min_id, max="+")
        return [_event_from_redis_entry(entry[1]) for entry in events]

    async def stream(self, session_id: str) -> AsyncIterator[Event]:
        """
        Stream events for a session.

        Captures the current tail cursor, then yields events appended after that
        cursor. Does NOT replay older historical events.
        """
        cursor = await self.get_stream_cursor(session_id)
        async for event in self.stream_after(session_id, cursor):
            yield event

    async def get_stream_cursor(self, session_id: str) -> str | None:
        """Return the current Redis stream cursor for a session."""
        stream_name = f"omnicoreagent_events:{session_id}"
        entries = await self.redis.xrevrange(stream_name, max="+", min="-", count=1)
        if not entries:
            return "0-0"
        return entries[0][0]

    async def stream_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> AsyncIterator[Event]:
        """Stream events appended after a specific Redis stream cursor."""
        stream_name = f"omnicoreagent_events:{session_id}"
        last_id = cursor or "0-0"
        while True:
            results = await self.redis.xread(
                {stream_name: last_id},
                block=1000,
                count=100,
            )
            if results:
                _, entries = results[0]
                for entry_id, data in entries:
                    last_id = entry_id
                    yield _event_from_redis_entry(data)


def _event_from_redis_entry(data: dict[str, str]) -> Event:
    event = Event.parse_raw(data["event"])
    sequence = data.get("sequence")
    if sequence is not None:
        event.sequence = int(sequence)
    return event
