"""
Event System Package

This package provides event handling and routing:
- BaseEventStore: Abstract base for event stores
- InMemoryEventStore: In-memory event storage
- RedisStreamEventStore: Redis stream-based events
- EventRouter: Routes events to appropriate handlers
"""

from .event_router import EventRouter
from .trace import AgentTrace, TraceStep, TraceSummary, build_event_trace

__all__ = [
    "AgentTrace",
    "EventRouter",
    "TraceStep",
    "TraceSummary",
    "build_event_trace",
]
