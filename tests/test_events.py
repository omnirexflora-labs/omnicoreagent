from omnicoreagent.core.events.base import (
    Event,
    EventType,
    ToolCallStartedPayload,
    UserMessagePayload,
)


def test_event_payloads_keep_model_dump_compatibility():
    payload = UserMessagePayload(message="hello")

    assert payload.model_dump() == {"message": "hello"}
    assert payload.dict() == {"message": "hello"}
    assert payload.json() == '{"message": "hello"}'


def test_event_serializes_and_parses_without_pydantic():
    event = Event(
        type=EventType.TOOL_CALL_STARTED,
        payload=ToolCallStartedPayload(tool_name="search", tool_args={"q": "test"}),
        agent_name="agent",
    )

    parsed = Event.parse_raw(event.json())

    assert parsed.type == EventType.TOOL_CALL_STARTED
    assert parsed.agent_name == "agent"
    assert parsed.payload.tool_name == "search"
    assert parsed.payload.tool_args == {"q": "test"}
    assert parsed.model_dump()["payload"] == {
        "tool_name": "search",
        "tool_args": {"q": "test"},
        "tool_call_id": None,
    }
