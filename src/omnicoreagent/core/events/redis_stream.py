import os

import redis.asyncio as redis
from typing import AsyncIterator, List
from omnicoreagent.core.events.base import BaseEventStore, Event

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class RedisStreamEventStore(BaseEventStore):
    """
    Redis Streams-based event store.
    
    Key design: stream() starts from '$' (latest) so only events published
    AFTER the stream call are received. Historical events are available via get_events().
    """
    
    def __init__(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)

    async def append(self, session_id: str, event: Event):
        """Append event to Redis stream."""
        stream_name = f"omnicoreagent_events:{session_id}"
        await self.redis.xadd(stream_name, {"event": event.json()})

    async def get_events(self, session_id: str) -> List[Event]:
        """Get all historical events for a session."""
        stream_name = f"omnicoreagent_events:{session_id}"
        events = await self.redis.xrange(stream_name, min="-", max="+")
        return [Event.parse_raw(entry[1]["event"]) for entry in events]

    async def stream(self, session_id: str) -> AsyncIterator[Event]:
        """
        Stream events for a session.
        
        Starts from '$' (latest) so only events published AFTER this call
        are received. Does NOT replay historical events.
        """
        stream_name = f"omnicoreagent_events:{session_id}"
        # Start from '$' = only new messages (not historical)
        last_id = "$"
        while True:
            results = await self.redis.xread({stream_name: last_id}, block=0, count=1)
            if results:
                _, entries = results[0]
                for entry_id, data in entries:
                    last_id = entry_id
                    yield Event.parse_raw(data["event"])
