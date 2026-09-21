"""Each message a model is sent, recorded once per trace.

Telemetry storage plan, T2. A model call used to record the whole
conversation it was sent, so a run's trace grew with the square of its
length and a long run was cut at the payload limit. Now:

- every message is a ``context_message`` event, once per trace, with its
  digest in the metadata (a payload can be cut; metadata is not);
- the tool catalog is a ``context_tools`` event, once per catalog;
- a model call records which messages it was sent: the previous call's list
  it extends (``extends``, ``keep``) and the digests it appends (``append``),
  or the whole list (``message_digests``) when it extends none.

``expand_model_contexts`` rebuilds every call's request from those records;
the trajectory reader and the exporters use it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from omnicoreagent.core.interaction_history import message_record, stable_message_digest

CONTEXT_REFERENCE_KEYS = frozenset(
    {"message_digests", "extends", "keep", "append", "tool_catalog_digest"}
)


@dataclass
class ContextRecording:
    """What one trace has recorded so far."""

    messages: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    # purpose -> (model call span id, its message digests)
    last: dict[str, tuple[str, list[str]]] = field(default_factory=dict)
    # (context assembly event id, its message digests)
    last_assembly: tuple[str, list[str]] | None = None


def digest_reference(
    previous: tuple[str, list[str]] | None, digests: list[str]
) -> dict[str, Any]:
    """A list of message digests as the previous list it extends and what it
    appends, or whole when it extends none: linear in a run's length."""
    keep = _common_prefix(previous[1], digests) if previous else 0
    if previous and keep:
        return {"extends": previous[0], "keep": keep, "append": digests[keep:]}
    return {"message_digests": digests}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _common_prefix(left: list[str], right: list[str]) -> int:
    length = 0
    for a, b in zip(left, right):
        if a != b:
            break
        length += 1
    return length


async def record_model_context(
    recorder: Any,
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    *,
    purpose: str,
) -> tuple[dict[str, Any], Callable[[str], None]]:
    """Record what is new in this call's context; return the reference the
    model call span carries, and a function to call with that span's id."""
    from omnicoreagent.core.telemetry.models import ActorType, TelemetryActor

    state: ContextRecording = recorder.context_recording()
    canonicalize = recorder.canonicalize_for_digest
    digests = [stable_message_digest(m, canonicalizer=canonicalize) for m in messages]
    actor = TelemetryActor(type=ActorType.MODEL)
    for digest, message in zip(digests, messages):
        if digest in state.messages:
            continue
        state.messages.add(digest)
        await recorder.emit_event(
            "context_message",
            actor=actor,
            input={"message": message_record(message)},
            metadata={"message_digest": digest},
        )
    tool_list = list(tools or [])
    tools_digest = _digest(canonicalize(tool_list))
    if tools_digest not in state.tools:
        state.tools.add(tools_digest)
        await recorder.emit_event(
            "context_tools",
            actor=actor,
            input={"tools": tool_list},
            metadata={"tool_catalog_digest": tools_digest},
        )

    reference: dict[str, Any] = {
        "tool_catalog_digest": tools_digest,
        **digest_reference(state.last.get(purpose), digests),
    }

    def commit(span_id: str) -> None:
        state.last[purpose] = (span_id, digests)

    return reference, commit


def expand_model_contexts(trace: Any) -> dict[str, dict[str, Any]]:
    """Every model call's messages and tools, rebuilt from the trace.

    Returns, per model call span id: ``messages``, ``tools`` and ``complete``
    (false when a message was cut or is missing from the trace). Calls that
    recorded their messages whole (older traces) are returned as recorded.
    """
    messages: dict[str, Any] = {}
    cut: set[str] = set()
    tools: dict[str, Any] = {}
    for event in trace.events:
        if event.event_type == "context_message":
            digest = (event.metadata or {}).get("message_digest")
            body = event.input or {}
            if digest is None:
                continue
            if "message" in body:
                messages[digest] = body["message"]
            else:
                messages[digest] = body
                cut.add(digest)
        elif event.event_type == "context_tools":
            digest = (event.metadata or {}).get("tool_catalog_digest")
            body = event.input or {}
            if digest is not None:
                tools[digest] = body.get("tools", body)
                if "tools" not in body:
                    cut.add(digest)

    requests = {
        span.span_id: span.input
        for span in trace.spans
        if span.kind == "model.call" and isinstance(span.input, dict)
    }
    digests_of: dict[str, list[str] | None] = {}

    def digests(span_id: str, seen: frozenset[str] = frozenset()) -> list[str] | None:
        # A call's list is its base's first `keep` digests and its own; the
        # base is resolved by id, so the order the store keeps spans in does
        # not matter.
        if span_id in digests_of:
            return digests_of[span_id]
        request = requests.get(span_id)
        result: list[str] | None = None
        if request is not None and span_id not in seen:
            if "message_digests" in request:
                result = list(request["message_digests"])
            elif "extends" in request:
                base = digests(request["extends"], seen | {span_id})
                if base is not None:
                    result = base[: int(request.get("keep") or 0)] + list(request.get("append") or [])
        digests_of[span_id] = result
        return result

    resolved: dict[str, dict[str, Any]] = {}
    for span_id, request in requests.items():
        if "messages" in request:
            resolved[span_id] = {
                "messages": request["messages"],
                "tools": request.get("tools"),
                "complete": True,
            }
            continue
        if not ({"message_digests", "extends"} & request.keys()):
            continue
        listed = digests(span_id)
        if listed is None:
            resolved[span_id] = {"messages": None, "tools": None, "complete": False}
            continue
        catalog = request.get("tool_catalog_digest")
        complete = all(d in messages and d not in cut for d in listed) and (
            catalog in tools and catalog not in cut
        )
        resolved[span_id] = {
            "messages": [messages.get(d) for d in listed],
            "tools": tools.get(catalog),
            "complete": complete,
        }
    return resolved


def with_expanded_model_inputs(trace: Any) -> Any:
    """A copy of the trace whose model call spans carry their whole request,
    for readers that take a span as it is (the exporters)."""
    expanded = expand_model_contexts(trace)
    if not expanded:
        return trace
    spans = []
    for span in trace.spans:
        whole = expanded.get(span.span_id)
        if whole is None or not isinstance(span.input, dict) or "messages" in span.input:
            spans.append(span)
            continue
        request = {k: v for k, v in span.input.items() if k not in CONTEXT_REFERENCE_KEYS}
        request.update({"messages": whole["messages"], "tools": whole["tools"]})
        spans.append(replace(span, input=request))
    return replace(trace, spans=spans)


def _resolve_lists(references: dict[str, dict[str, Any]]) -> dict[str, list[str] | None]:
    """Each id's full digest list, following ``extends`` by id."""
    resolved: dict[str, list[str] | None] = {}

    def resolve(key: str, seen: frozenset[str] = frozenset()) -> list[str] | None:
        if key in resolved:
            return resolved[key]
        reference = references.get(key)
        result: list[str] | None = None
        if reference is not None and key not in seen:
            if "message_digests" in reference:
                result = list(reference["message_digests"])
            elif "extends" in reference:
                base = resolve(reference["extends"], seen | {key})
                if base is not None:
                    result = base[: int(reference.get("keep") or 0)] + list(reference.get("append") or [])
        resolved[key] = result
        return result

    for key in references:
        resolve(key)
    return resolved


def context_assembly_digests(trace: Any) -> dict[str, list[str] | None]:
    """Each context assembly event's message digests, in order of the
    context; recorded at every capture level, content never."""
    return _resolve_lists(
        {
            event.event_id: event.output
            for event in trace.events
            if event.event_type == "context_assembly" and isinstance(event.output, dict)
        }
    )
