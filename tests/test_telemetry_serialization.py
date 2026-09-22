"""Audit A2: recording something costs one walk of it, not two.

``to_plain`` turned every dataclass into a dict with ``dataclasses.asdict``,
which already walks the whole object and deep-copies its leaves, and then
walked the result again itself. Every event, span, and payload paid for that
twice on the way in. It now walks once, and this file holds it to that: the
same output as before, and a node count that cannot quietly grow again.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from omnicoreagent.core.telemetry import models as telemetry_models
from omnicoreagent.core.telemetry.models import (
    ActorType,
    SpanStatus,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    to_plain,
)


def _the_old_way(value: Any) -> Any:
    """What ``to_plain`` used to do, kept here as the thing to match."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if is_dataclass(value):
        return {key: _the_old_way(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _the_old_way(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_the_old_way(item) for item in value]
    if isinstance(value, tuple):
        return [_the_old_way(item) for item in value]
    return value


def _an_event() -> TelemetryEvent:
    return TelemetryEvent(
        trace_id="trace_1",
        span_id="span_1",
        event_type="tool_result",
        actor=TelemetryActor(type=ActorType.TOOL, name="lookup"),
        input={"tool_args": {"key": "a", "nested": [1, 2, {"deep": True}]}},
        output={"rows": [{"id": 1}, {"id": 2}], "when": datetime.now(timezone.utc)},
        metadata={"phase": "result", "tags": ("a", "b")},
    )


def _a_span() -> TelemetrySpan:
    return TelemetrySpan(
        trace_id="trace_1",
        name="lookup",
        kind="tool.call",
        actor=TelemetryActor(type=ActorType.TOOL, name="lookup"),
        status=SpanStatus.OK,
        input={"tool_args": {"key": "a"}},
        attributes={"tool_provider": "local", "retries": [1, 2, 3]},
    )


def test_what_is_recorded_is_what_was_recorded_before():
    for record in (_an_event(), _a_span(), {"a": [1, {"b": (2, 3)}]}, ["x", None]):
        assert to_plain(record) == _the_old_way(record)


def test_the_record_is_rebuilt_rather_than_shared():
    """A recorded payload must not change when the caller changes theirs."""
    event = _an_event()

    recorded = to_plain(event)
    event.input["tool_args"]["nested"].append("added afterwards")
    event.metadata["phase"] = "changed afterwards"

    assert recorded["input"]["tool_args"]["nested"] == [1, 2, {"deep": True}]
    assert recorded["metadata"]["phase"] == "result"


def test_recording_does_not_copy_the_record_before_walking_it():
    """The guard: no ``asdict``.

    ``dataclasses.asdict`` walks the whole object and deep-copies its leaves
    before ``to_plain`` has looked at any of it, so using it means recording
    something costs two walks and a copy. Counting ``to_plain`` calls cannot
    see that second walk, because it happens inside ``asdict`` — so this
    watches for ``asdict`` itself.
    """
    used = []
    import dataclasses

    original = dataclasses.asdict

    def watched(*args, **kwargs):
        used.append(args[0] if args else None)
        return original(*args, **kwargs)

    dataclasses.asdict = watched
    try:
        to_plain(_an_event())
        to_plain(_a_span())
    finally:
        dataclasses.asdict = original

    assert used == [], f"recording copied {len(used)} record(s) before reading them"


def test_recording_walks_each_node_once():
    """One call per node, so a second walk cannot creep back in."""
    walked = []
    original = telemetry_models.to_plain

    def counted(value):
        walked.append(value)
        return original(value)

    telemetry_models.to_plain = counted
    try:
        # 1 dict + 2 values, one of them a list of 2: 5 nodes in all.
        counted({"a": 1, "b": [2, 3]})
        simple = len(walked)
    finally:
        telemetry_models.to_plain = original

    assert simple == 5, walked


def test_a_dataclass_inside_a_payload_is_still_turned_into_a_dict():
    event = _an_event()
    event.output = {"actor": TelemetryActor(type=ActorType.USER, name="ada")}

    assert to_plain(event)["output"] == {"actor": {"type": "user", "name": "ada", "id": None}}
