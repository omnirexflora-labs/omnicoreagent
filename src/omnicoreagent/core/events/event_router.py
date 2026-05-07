"""Event router for dynamic event store selection."""

from omnicoreagent._optional import load_optional
from omnicoreagent.core.events.base import BaseEventStore, Event
from omnicoreagent.core.events.in_memory import InMemoryEventStore
from omnicoreagent.core.events.trace import AgentTrace, build_event_trace
from omnicoreagent.core.logging import logger


class EventRouter:
    """Router for managing different event store backends."""

    def __init__(self, event_store_type: str = "in_memory"):
        """
        Initialize EventRouter.

        Args:
            event_store_type: Type of event store ("in_memory", "redis_stream")
        """
        self.event_store_type = event_store_type
        self._event_store: BaseEventStore | None = None

        self._initialize_event_store()

    def __str__(self):
        """Return a readable string representation of the EventRouter."""
        store_type = self.event_store_type
        available = self.is_available()
        return f"EventRouter(type={store_type}, available={available})"

    def __repr__(self):
        """Return a detailed representation of the EventRouter."""
        return self.__str__()

    def _initialize_event_store(self):
        """Initialize the event store based on type."""
        try:
            if self.event_store_type == "redis_stream":
                RedisStreamEventStore = load_optional(
                    "Redis stream events",
                    "redis",
                    lambda: __import__(
                        "omnicoreagent.core.events.redis_stream",
                        fromlist=["RedisStreamEventStore"],
                    ).RedisStreamEventStore,
                )
                self._event_store = RedisStreamEventStore()
                logger.info("Initialized Redis Stream Event Store")
            elif self.event_store_type == "in_memory":
                self._event_store = InMemoryEventStore()
                logger.info("Initialized In-Memory Event Store")
            else:
                logger.warning(
                    f"Unknown event store type: {self.event_store_type}. Falling back to memory."
                )
                self._event_store = InMemoryEventStore()

        except Exception as e:
            logger.error(
                f"Failed to initialize {self.event_store_type} event store: {e}"
            )
            logger.info("Falling back to in-memory event store")
            self._event_store = InMemoryEventStore()

    async def append(self, session_id: str, event: Event) -> None:
        """Append an event to the current event store."""
        if not self._event_store:
            raise RuntimeError("No event store available")

        await self._event_store.append(session_id=session_id, event=event)

    async def get_events(self, session_id: str) -> list[Event]:
        """Get events from the current event store."""
        if not self._event_store:
            raise RuntimeError("No event store available")

        return await self._event_store.get_events(session_id=session_id)

    async def get_trace(self, session_id: str) -> AgentTrace:
        """Build an internal trace from stored session events."""
        events = await self.get_events(session_id=session_id)
        return build_event_trace(session_id=session_id, events=events)

    async def stream(self, session_id: str):
        """Stream events from the current event store."""
        if not self._event_store:
            raise RuntimeError("No event store available")

        async for event in self._event_store.stream(session_id=session_id):
            yield event

    def get_event_store_type(self) -> str:
        """Get the current event store type."""
        return self.event_store_type

    def is_available(self) -> bool:
        """Check if the event store is available."""
        return self._event_store is not None

    def get_event_store_info(self) -> dict[str, object]:
        """Get information about the current event store."""
        return {"type": self.event_store_type, "available": self.is_available()}

    def switch_event_store(self, event_store_type: str):
        """Switch to a different event store type."""
        if event_store_type == self.event_store_type:
            logger.info(f"Event store already set to {event_store_type}")
            return

        logger.info(
            f"Switching event store from {self.event_store_type} to {event_store_type}"
        )

        self.event_store_type = event_store_type
        self._initialize_event_store()
