"""Stream assembly keeps provider continuation data exactly.

Chunks are real LiteLLM stream chunks: from the fake servers in
``tests/fixtures/continuation_providers.py`` through LiteLLM's provider code,
and from raw Anthropic events through LiteLLM's own Anthropic stream parser.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from continuation_providers import (  # noqa: E402
    GEMINI_SIGNATURE,
    OPENROUTER_SIGNATURE,
    REDACTED,
    SIGNATURE,
    THINKING,
    ContinuationProviders,
)

from omnicoreagent.core.agents.llm_response import normalize_model_turn  # noqa: E402
from omnicoreagent.core.model_stream import ModelStreamAssembler  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
        },
    }
]


@pytest.fixture
def providers(monkeypatch):
    fake = ContinuationProviders()
    monkeypatch.setenv("ANTHROPIC_API_BASE", fake.base)
    monkeypatch.setenv("GEMINI_API_BASE", fake.base)
    monkeypatch.setenv("OPENROUTER_API_BASE", f"{fake.base}/api/v1")
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "fake-key")
    yield fake
    fake.close()


async def _turns(model: str):
    """The same first turn, streamed and not, through LiteLLM."""
    import litellm

    request = {
        "model": model,
        "messages": [{"role": "user", "content": "Status of A-17?"}],
        "tools": TOOLS,
        "num_retries": 0,
    }
    plain = normalize_model_turn(await litellm.acompletion(**request))
    assembler = ModelStreamAssembler()
    async for chunk in await litellm.acompletion(**request, stream=True):
        assembler.feed(chunk)
    return plain, assembler.finish()


@pytest.mark.asyncio
async def test_streamed_anthropic_thinking_matches_the_non_streamed_turn(providers):
    plain, streamed = await _turns("anthropic/claude-sonnet-4-5")

    expected = [
        {"type": "thinking", "thinking": THINKING, "signature": SIGNATURE},
        {"type": "redacted_thinking", "data": REDACTED},
    ]
    assert streamed.provider_fields["thinking_blocks"] == expected
    assert plain.provider_fields["thinking_blocks"] == expected
    assert streamed.provider_fields["reasoning_content"] == THINKING


@pytest.mark.asyncio
async def test_streamed_gemini_signature_stays_on_its_tool_call(providers):
    plain, streamed = await _turns("gemini/gemini-3-pro-preview")

    [call] = streamed.tool_calls
    assert call.provider_fields["provider_specific_fields"] == {
        "thought_signature": GEMINI_SIGNATURE
    }
    assert call.as_dict() == plain.tool_calls[0].as_dict() | {"id": call.id}


@pytest.mark.asyncio
async def test_streamed_openrouter_reasoning_details_are_kept_like_the_non_streamed_turn(providers):
    plain, streamed = await _turns("openrouter/anthropic/claude-sonnet-4.5")

    details = streamed.provider_fields["provider_specific_fields"]["reasoning_details"]
    assert details[0]["signature"] == OPENROUTER_SIGNATURE
    assert details == plain.provider_fields["provider_specific_fields"]["reasoning_details"]


def _anthropic_chunks(events):
    """Raw Anthropic events through LiteLLM's own Anthropic stream parser."""
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator

    parser = ModelResponseIterator(streaming_response=iter(()), sync_stream=True)
    return [parser.chunk_parser(event) for event in events]


def test_two_thinking_blocks_in_one_message_stay_separate():
    def thinking(index, pieces, signature):
        return [
            {"type": "content_block_start", "index": index,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            *[
                {"type": "content_block_delta", "index": index,
                 "delta": {"type": "thinking_delta", "thinking": piece}}
                for piece in pieces
            ],
            {"type": "content_block_delta", "index": index,
             "delta": {"type": "signature_delta", "signature": signature}},
            {"type": "content_block_stop", "index": index},
        ]

    events = [
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
            "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
        *thinking(0, ["First ", "thought."], "sig-one"),
        *thinking(1, ["Second ", "thought."], "sig-two"),
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "lookup_order", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '{"order_id": "A-17"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},
    ]
    assembler = ModelStreamAssembler()
    for chunk in _anthropic_chunks(events):
        assembler.feed(chunk)
    turn = assembler.finish()

    assert turn.provider_fields["thinking_blocks"] == [
        {"type": "thinking", "thinking": "First thought.", "signature": "sig-one"},
        {"type": "thinking", "thinking": "Second thought.", "signature": "sig-two"},
    ]
    assert turn.tool_calls[0].name == "lookup_order"


def test_reasoning_details_streamed_in_pieces_are_merged_per_index():
    assembler = ModelStreamAssembler()
    pieces = [
        {"type": "reasoning.text", "text": "The user ", "index": 0, "format": "anthropic-claude-v1"},
        {"type": "reasoning.text", "text": "wants A-17.", "index": 0},
        {"type": "reasoning.text", "text": "", "index": 0, "signature": OPENROUTER_SIGNATURE},
        {"type": "reasoning.encrypted", "data": "gAAAAB-opaque", "index": 1},
    ]
    for piece in pieces:
        assembler.feed({"choices": [{"index": 0, "delta": {"content": "", "reasoning_details": [piece]}}]})
    assembler.feed({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    turn = assembler.finish()

    assert turn.provider_fields["provider_specific_fields"]["reasoning_details"] == [
        {
            "type": "reasoning.text",
            "text": "The user wants A-17.",
            "index": 0,
            "format": "anthropic-claude-v1",
            "signature": OPENROUTER_SIGNATURE,
        },
        {"type": "reasoning.encrypted", "data": "gAAAAB-opaque", "index": 1},
    ]
