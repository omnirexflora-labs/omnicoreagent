"""Serialization helpers for OmniServe API responses."""

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from omnicoreagent.core.privacy import PrivacyFilter


def to_plain_dict(value: Any) -> dict[str, Any]:
    """Convert common runtime objects into JSON-ready dictionaries."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return {
            key: item for key, item in value.__dict__.items() if not key.startswith("_")
        }
    return {"data": str(value)}


def normalize_metric(metric: Any) -> dict[str, Any] | None:
    """Normalize an optional runtime metric object for API output."""
    if metric is None:
        return None
    return to_plain_dict(metric)


def normalize_run_result(
    result: Any,
    *,
    agent_name: str,
    privacy_filter: PrivacyFilter | None = None,
) -> dict[str, Any]:
    """Normalize agent.run output into the stable OmniServe response shape."""
    if isinstance(result, dict):
        normalized = {
            "response": result.get("response", ""),
            "agent_name": result.get("agent_name", agent_name),
            "metric": normalize_metric(result.get("metric")),
            "trace_id": result.get("trace_id"),
            "run_id": result.get("run_id"),
            **{
                key: result[key]
                for key in (
                    "status",
                    "termination_reason",
                    "guardrail_result",
                    "approvals",
                    "budget_request",
                )
                if key in result
            },
        }
    else:
        normalized = {
            "response": str(result),
            "agent_name": agent_name,
            "metric": None,
        }
    if privacy_filter is not None:
        return privacy_filter.redact(normalized, boundary="public")
    return normalized


def normalize_event(event: Any) -> dict[str, Any]:
    """Normalize an event object or mapping for JSON/SSE output."""
    return to_plain_dict(event)


def normalize_events(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize event objects for JSON output."""
    return [normalize_event(event) for event in events]
