"""A normalized model turn keeps the provider's continuation data unchanged.

Responses are real LiteLLM ``ModelResponse`` objects, produced by LiteLLM's
Anthropic, Gemini, and OpenRouter code from the fake servers in
``tests/fixtures/continuation_providers.py``.
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
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest  # noqa: E402

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


async def _first_turn(model: str):
    import litellm

    return await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": "Status of A-17?"}],
        tools=TOOLS,
        num_retries=0,
    )


@pytest.mark.asyncio
async def test_anthropic_thinking_blocks_survive_normalization(providers):
    turn = normalize_model_turn(await _first_turn("anthropic/claude-sonnet-4-5"))

    expected = [
        {"type": "thinking", "thinking": THINKING, "signature": SIGNATURE},
        {"type": "redacted_thinking", "data": REDACTED},
    ]
    message = turn.assistant_message()
    assert message["thinking_blocks"] == expected
    assert turn.provider_fields["thinking_blocks"] == expected


@pytest.mark.asyncio
async def test_gemini_signature_survives_on_the_tool_call(providers):
    turn = normalize_model_turn(await _first_turn("gemini/gemini-3-pro-preview"))

    [call] = turn.assistant_message()["tool_calls"]
    assert call["provider_specific_fields"] == {"thought_signature": GEMINI_SIGNATURE}
    assert turn.tool_calls[0].provider_fields == {
        "provider_specific_fields": {"thought_signature": GEMINI_SIGNATURE}
    }


@pytest.mark.asyncio
async def test_openrouter_reasoning_details_survive_normalization(providers):
    turn = normalize_model_turn(await _first_turn("openrouter/anthropic/claude-sonnet-4.5"))

    fields = turn.assistant_message()["provider_specific_fields"]
    assert fields["reasoning_details"][0]["signature"] == OPENROUTER_SIGNATURE


def test_mapping_responses_keep_the_same_fields():
    message = {
        "role": "assistant",
        "content": None,
        "thinking_blocks": [{"type": "redacted_thinking", "data": REDACTED}],
        "reasoning_items": [{"id": "rs_1", "encrypted_content": "gAAAA"}],
        "provider_specific_fields": {"reasoning_details": [{"type": "reasoning.encrypted"}]},
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "provider_specific_fields": {"thought_signature": "sig"},
                "function": {
                    "name": "lookup_order",
                    "arguments": "{}",
                    "provider_specific_fields": {"thought_signature": "sig"},
                },
            }
        ],
    }
    turn = normalize_model_turn({"choices": [{"message": message, "finish_reason": "tool_calls"}]})

    sent = turn.assistant_message()
    for key in ("thinking_blocks", "reasoning_items", "provider_specific_fields"):
        assert sent[key] == message[key], key
    assert sent["tool_calls"] == message["tool_calls"]


def test_turns_without_continuation_data_are_unchanged():
    turn = ModelTurn(content="hi", tool_calls=(ToolRequest("c1", "f", "{}"),))

    assert turn.assistant_message() == {
        "role": "assistant",
        "content": "hi",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    }


def test_continuation_data_is_a_copy_not_a_reference():
    blocks = [{"type": "thinking", "thinking": "t", "signature": "s"}]
    turn = normalize_model_turn(
        {"choices": [{"message": {"role": "assistant", "content": "x", "thinking_blocks": blocks}}]}
    )
    turn.assistant_message()["thinking_blocks"][0]["thinking"] = "changed"
    blocks[0]["signature"] = "changed"

    assert turn.provider_fields["thinking_blocks"] == [
        {"type": "thinking", "thinking": "t", "signature": "s"}
    ]


def test_empty_placeholder_fields_are_not_continuation_data():
    turn = normalize_model_turn(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Order A-17 has shipped.",
                        "provider_specific_fields": {"citations": None, "thinking_blocks": []},
                    }
                }
            ]
        }
    )

    assert turn.provider_fields == {}
