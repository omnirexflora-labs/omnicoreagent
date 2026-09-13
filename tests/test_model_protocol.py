from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.llm_response import (
    normalize_model_turn,
    extract_response_content,
)
from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.core.model_protocol import ToolRequest
from omnicoreagent.core.types import Message


def response(content=None, calls=None, finish="tool_calls"):
    return {
        "choices": [
            {
                "message": {"content": content, "tool_calls": calls},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }


def call(id="call_1", args='{"value":"001"}'):
    return {
        "id": id,
        "type": "function",
        "function": {"name": "lookup", "arguments": args},
    }


def as_object(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: as_object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [as_object(item) for item in value]
    return value


@pytest.mark.parametrize("convert", [lambda value: value, as_object])
@pytest.mark.parametrize("content", [None, "Looking up both records."])
def test_model_turn_retains_structured_calls(convert, content):
    turn = normalize_model_turn(
        convert(response(content, [call(), call("call_2", '{"value":false}')]))
    )
    assert turn.content == content
    assert turn.finish_reason == "tool_calls"
    assert turn.usage.total_tokens == 10
    assert [item.id for item in turn.tool_calls] == ["call_1", "call_2"]
    assert [item.decode_arguments() for item in turn.tool_calls] == [
        {"value": "001"},
        {"value": False},
    ]
    assert turn.assistant_message()["tool_calls"] == [
        call(),
        call("call_2", '{"value":false}'),
    ]


def test_model_turn_retains_blocks_and_joins_all_text():
    blocks = [
        {"type": "text", "text": "first "},
        {"type": "image_url", "image_url": {"url": "example"}},
        {"type": "text", "text": "second"},
    ]
    turn = normalize_model_turn(response(blocks, finish="stop"))
    assert turn.content == blocks
    assert turn.text == "first second"
    assert extract_response_content(response(blocks, finish="stop")) == "first second"


@pytest.mark.parametrize(
    "value",
    [
        response(calls=[call("")]),
        response(calls=[call(None)]),
        response(calls=[call(), call()]),
        {"choices": []},
        None,
    ],
)
def test_malformed_provider_response_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_model_turn(value)


@pytest.mark.parametrize("args", ["[]", "null", '{"a":NaN}', '{"a":1,"a":2}', "{"])
def test_invalid_arguments_fail_only_when_decoding_the_identified_call(args):
    turn = normalize_model_turn(response(calls=[call(args=args)]))
    assert turn.tool_calls[0].id == "call_1"
    with pytest.raises(ValueError):
        turn.tool_calls[0].decode_arguments()


def test_argument_decoding_preserves_json_values_exactly():
    request = ToolRequest(
        "call_1",
        "lookup",
        '{"id":"001","flag":"false","text":"hello, world",'
        '"items":[{"xml":"<tool_call/>"}],"zero":0,"empty":null}',
    )
    assert request.decode_arguments() == {
        "id": "001",
        "flag": "false",
        "text": "hello, world",
        "items": [{"xml": "<tool_call/>"}],
        "zero": 0,
        "empty": None,
    }


def test_text_extractor_does_not_silently_discard_calls():
    with pytest.raises(ValueError, match="text-only"):
        extract_response_content(response("working", [call()]), default="")


def test_finish_and_refusal_are_retained_without_claiming_success():
    raw = response("partial", finish="length")
    raw["choices"][0]["message"]["refusal"] = "refused"
    turn = normalize_model_turn(raw)
    assert turn.finish_reason == "length"
    assert turn.refusal == "refused"


def test_provider_messages_exclude_internal_metadata():
    connection = LLMConnection(
        {"provider": "openai", "model": "test", "api_key": "test"}
    )
    message = Message(
        role="assistant",
        content="working",
        tool_calls=[call()],
        metadata={"secret_internal": "private"},
        timestamp="yesterday",
    )
    payload = connection._completion_params(
        [
            message,
            {
                "role": "tool",
                "content": "found",
                "tool_call_id": "call_1",
                "session_id": "internal",
                "metadata": {"tool": "lookup"},
            },
        ]
    )
    assert payload["messages"] == [
        {"role": "assistant", "content": "working", "tool_calls": [call()]},
        {"role": "tool", "content": "found", "tool_call_id": "call_1"},
    ]
