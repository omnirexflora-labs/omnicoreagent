import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnicoreagent.core.model_stream import ModelStreamAssembler
from omnicoreagent.core.llm import LLMConnection


def chunk(text=None, calls=None, finish=None):
    return {
        "choices": [
            {
                "index": 0,
                "delta": {"content": text, "tool_calls": calls},
                "finish_reason": finish,
            }
        ]
    }


def delta(index, args, id=None, name=None):
    return {"index": index, "id": id, "function": {"name": name, "arguments": args}}


def test_interleaved_call_fragments_and_final_usage_are_retained():
    assembler = ModelStreamAssembler()
    assert assembler.feed(chunk("Working")) == [
        {"type": "text_delta", "text": "Working"}
    ]
    assembler.feed(
        chunk(
            calls=[
                delta(1, '{"b":', "second", "beta"),
                delta(0, '{"a":', "first", "alpha"),
            ]
        )
    )
    assembler.feed(
        chunk(calls=[delta(0, '"001"}'), delta(1, "false}")], finish="tool_calls")
    )
    assembler.feed(
        {
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
        }
    )
    turn = assembler.finish()
    assert [call.id for call in turn.tool_calls] == ["first", "second"]
    assert [call.decode_arguments() for call in turn.tool_calls] == [
        {"a": "001"},
        {"b": False},
    ]
    assert turn.usage.total_tokens == 6
    assert turn.text == "Working"


def test_truncated_stream_cannot_produce_an_executable_turn():
    assembler = ModelStreamAssembler()
    assembler.feed(chunk(calls=[delta(0, '{"a":', "first", "alpha")]))
    with pytest.raises(ValueError, match="terminal finish"):
        assembler.finish()


def test_stream_refusal_and_length_finish_are_retained():
    assembler = ModelStreamAssembler()
    assembler.feed(
        {
            "choices": [
                {"delta": {"refusal": "refused"}, "finish_reason": "content_filter"}
            ]
        }
    )
    assert assembler.finish().refusal == "refused"
    assert assembler.finish().finish_reason == "content_filter"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "cencori"])
async def test_provider_yields_before_completion_and_closes_stream(
    monkeypatch, provider
):
    ready = asyncio.Event()
    closed = []

    async def chunks():
        try:
            yield chunk("first")
            await ready.wait()
            yield chunk(" second", finish="stop")
        finally:
            closed.append(True)

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=chunks()))
        ),
        close=AsyncMock(),
    )
    if provider == "cencori":
        monkeypatch.setattr(
            "omnicoreagent.core.llm._get_openai",
            lambda: SimpleNamespace(AsyncOpenAI=lambda **kwargs: client),
        )
        create = client.chat.completions.create
    else:
        create = AsyncMock(return_value=chunks())
        monkeypatch.setattr(
            "omnicoreagent.core.llm._get_litellm",
            lambda: SimpleNamespace(acompletion=create),
        )
    connection = LLMConnection(
        {"provider": provider, "model": "test", "api_key": "test"}
    )
    stream = connection.llm_stream([{"role": "user", "content": "hello"}])
    assert await asyncio.wait_for(anext(stream), 1) == {
        "type": "text_delta",
        "text": "first",
    }
    assert not ready.is_set()
    ready.set()
    events = [event async for event in stream]
    assert events[-1]["turn"].text == "first second"
    assert create.call_args.kwargs["stream"] is True
    assert create.call_args.kwargs["stream_options"] == {"include_usage": True}
    assert closed == [True]
    if provider == "cencori":
        client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_closing_public_provider_iterator_closes_upstream(monkeypatch):
    closed = asyncio.Event()

    async def chunks():
        try:
            yield chunk("partial")
            await asyncio.Event().wait()
        finally:
            closed.set()

    create = AsyncMock(return_value=chunks())
    monkeypatch.setattr(
        "omnicoreagent.core.llm._get_litellm",
        lambda: SimpleNamespace(acompletion=create),
    )
    stream = LLMConnection(
        {"provider": "openai", "model": "test", "api_key": "test"}
    ).llm_stream([])
    await anext(stream)
    await stream.aclose()
    assert closed.is_set()
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_stream_failure_after_text_is_not_retried(monkeypatch):
    async def chunks():
        yield chunk("partial")
        raise RuntimeError("connection interrupted")

    create = AsyncMock(return_value=chunks())
    monkeypatch.setattr(
        "omnicoreagent.core.llm._get_litellm",
        lambda: SimpleNamespace(acompletion=create),
    )
    stream = LLMConnection(
        {"provider": "openai", "model": "test", "api_key": "test"}
    ).llm_stream([])
    assert (await anext(stream))["text"] == "partial"
    with pytest.raises(RuntimeError, match="connection interrupted"):
        await anext(stream)
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_complete_request_retry_waits_asynchronously(monkeypatch):
    create = AsyncMock(side_effect=[RuntimeError("rate limit"), {"choices": []}])
    sleep = AsyncMock()
    monkeypatch.setattr(
        "omnicoreagent.core.llm._get_litellm",
        lambda: SimpleNamespace(acompletion=create),
    )
    monkeypatch.setattr("omnicoreagent.core.llm.asyncio.sleep", sleep)
    result = await LLMConnection(
        {"provider": "openai", "model": "test", "api_key": "test"}
    ).llm_call([])
    assert result == {"choices": []}
    assert create.await_count == 2
    sleep.assert_awaited_once()


def test_normalized_turn_retains_stream_usage():
    from omnicoreagent.core.agents.llm_response import extract_response_usage
    from omnicoreagent.core.model_protocol import ModelTurn
    from omnicoreagent.core.token_usage import Usage

    usage = Usage(requests=1, request_tokens=12, response_tokens=3, total_tokens=15)
    assert extract_response_usage(ModelTurn(content="done", usage=usage)) == usage
