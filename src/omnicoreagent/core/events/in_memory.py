import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass

from omnicoreagent.core.events.base import BaseEventStore, Event, validate_event

_SUBSCRIBER_QUEUE_SIZE = 1000


@dataclass
class _StreamOverflow:
    message: str


class InMemoryEventStore(BaseEventStore):
    """
    In-memory event store with pub/sub streaming.

    Key design: Each stream() call creates a NEW subscriber queue.
    Events are only delivered to subscribers that were listening WHEN the event was published.
    This prevents old events from being replayed to new requests.
    """

    def __init__(self):
        self.logs: dict[str, list[Event]] = defaultdict(list)
        self._sequences: dict[str, int] = defaultdict(int)
        # Map session_id -> set of subscriber queues
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def append(self, session_id: str, event: Event) -> None:
        """Store event and broadcast to all active subscribers."""
        validate_event(event)
        async with self._lock:
            self._sequences[session_id] += 1
            event.sequence = self._sequences[session_id]
            stored_event = Event.parse_raw(event.json())
            self.logs[session_id].append(stored_event)
            dead_queues = set()
            for queue in self._subscribers[session_id]:
                try:
                    queue.put_nowait(stored_event)
                except asyncio.QueueFull:
                    await self._notify_overflow(queue)
                    dead_queues.add(queue)
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
        cursor = await self.get_stream_cursor(session_id)
        async for event in self.stream_after(session_id, cursor):
            yield event

    async def get_stream_cursor(self, session_id: str) -> str | None:
        """Return the current per-session stream cursor."""
        async with self._lock:
            return str(self._sequences[session_id])

    async def stream_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> AsyncIterator[Event]:
        """Stream stored and live events after the given per-session cursor."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)

        async with self._lock:
            self._subscribers[session_id].add(queue)
            replay_events = self._events_after_cursor_unlocked(session_id, cursor)

        try:
            for event in replay_events:
                yield event

            while True:
                event = await queue.get()
                if isinstance(event, _StreamOverflow):
                    raise RuntimeError(event.message)
                yield event
        finally:
            async with self._lock:
                self._subscribers[session_id].discard(queue)

    async def get_events_after(
        self,
        session_id: str,
        cursor: str | None,
    ) -> list[Event]:
        """Return a snapshot of events appended after the given cursor."""
        async with self._lock:
            return self._events_after_cursor_unlocked(session_id, cursor)

    def _events_after_cursor_unlocked(
        self,
        session_id: str,
        cursor: str | None,
    ) -> list[Event]:
        after_sequence = int(cursor or 0)
        return [
            event
            for event in self.logs[session_id]
            if event.sequence is not None and event.sequence > after_sequence
        ]

    async def _notify_overflow(self, queue: asyncio.Queue) -> None:
        overflow = _StreamOverflow("In-memory event stream subscriber overflow")
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        await queue.put(overflow)
