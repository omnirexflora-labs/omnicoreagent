import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from omnicoreagent.core.events.base import BaseEventStore, Event


class InMemoryEventStore(BaseEventStore):
    """
    In-memory event store with pub/sub streaming.

    Key design: Each stream() call creates a NEW subscriber queue.
    Events are only delivered to subscribers that were listening WHEN the event was published.
    This prevents old events from being replayed to new requests.
    """

    def __init__(self):
        self.logs: dict[str, list[Event]] = defaultdict(list)
        # Map session_id -> set of subscriber queues
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def append(self, session_id: str, event: Event) -> None:
        """Store event and broadcast to all active subscribers."""
        async with self._lock:
            self.logs[session_id].append(event)
            dead_queues = set()
            for queue in self._subscribers[session_id]:
                try:
                    queue.put_nowait(event)
                except Exception:
                    dead_queues.add(queue)
            # Clean up dead queues
            self._subscribers[session_id] -= dead_queues

    async def get_events(self, session_id: str) -> list[Event]:
        """Get all historical events for a session."""
        async with self._lock:
            return list(self.logs[session_id])

    async def stream(self, session_id: str) -> AsyncIterator[Event]:
        """
        Stream events for a session.

        Creates a NEW queue for THIS subscriber - only receives events
        published AFTER this call. Does NOT replay historical events.
        """
        # Create a fresh queue for this subscriber
        queue: asyncio.Queue = asyncio.Queue()

        # Register subscriber
        async with self._lock:
            self._subscribers[session_id].add(queue)

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            # Unregister on disconnect
            async with self._lock:
                self._subscribers[session_id].discard(queue)
