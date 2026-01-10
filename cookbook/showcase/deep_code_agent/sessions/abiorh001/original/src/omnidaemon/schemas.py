from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, Optional, Callable, Awaitable, List
import uuid
import time


class PayloadBase(BaseModel):
    content: str = Field(..., description="The main content of the payload.")
    webhook: Optional[str] = Field(
        None, description="Optional webhook URL for callbacks."
    )


class EventEnvelope(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique event ID (UUID4).",
    )
    topic: Optional[str] = Field(
        None, description="Event topic (e.g., 'file_system.tasks')."
    )
    payload: PayloadBase = Field(..., description="Business payload.")
    tenant_id: Optional[str] = Field(
        None, description="Tenant identifier for multi-tenancy isolation."
    )
    created_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp (seconds) when event was created.",
    )
    delivery_attempts: int = Field(
        default=1,
        ge=1,
        description="Number of times this event has been delivered (incremented on retry).",
    )
    correlation_id: Optional[str] = Field(
        None,
        description="ID to trace a request flow across services (e.g., HTTP X-Correlation-ID).",
    )
    causation_id: Optional[str] = Field(
        None, description="ID of the event/command that caused this event."
    )
    source: str = Field(
        default="unknown",
        description="Service or component that published this event (e.g., 'web-api', 'scheduler').",
    )
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata (e.g., user_id, trace_id, flags).",
    )

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Topic must be a non-empty string")
        if v.startswith("omni-dlq:") or ":dlq" in v:
            raise ValueError(
                "Topic must not contain 'dlq' (reserved for dead-letter queues)"
            )
        return v.strip()


class SubscriptionConfig(BaseModel):
    reclaim_idle_ms: Optional[int] = Field(
        None,
        description="Time in milliseconds after which idle messages are reclaimed if the event bus supports it.",
    )
    dlq_retry_limit: Optional[int] = Field(
        None,
        description="Number of retries before sending message to dead-letter queue if the event bus supports it.",
    )
    consumer_count: Optional[int] = Field(
        1,
        ge=1,
        description="Number of parallel consumers for this subscription (if supported by the event bus).",
    )


class AgentConfig(BaseModel):
    name: str = Field(
        default_factory=lambda: f"agent-{str(uuid.uuid4())}",
        description="Unique name for the agent.",
    )
    topic: str = Field(..., description="Topic to which the agent subscribes.")
    callback: Callable[[Dict[str, Any]], Awaitable[Any]] = Field(
        ..., description="Async function to handle incoming messages."
    )
    tools: Optional[list[str]] = Field(
        default_factory=list, description="List of tools available to the agent."
    )
    description: Optional[str] = Field(
        "", description="Description of the agent's purpose."
    )
    config: Optional[SubscriptionConfig] = Field(
        default_factory=SubscriptionConfig,
        description="Configuration for the agent's subscription.",
    )
