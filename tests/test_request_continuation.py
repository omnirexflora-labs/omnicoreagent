"""Each provider's request carries the continuation data it needs, and only that.

LiteLLM passes unknown message fields through to OpenAI-compatible providers
unchanged, so data meant for one provider must never reach another.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.llm import LLMConnection

THINKING = [
    {"type": "thinking", "thinking": "Check the order.", "signature": "sig-anthropic"},
    {"type": "redacted_thinking", "data": "opaque"},
]
DETAILS = [{"type": "reasoning.text", "text": "Check.", "signature": "sig-openrouter", "index": 0}]
ASSISTANT = {
    "role": "assistant",
    "content": None,
    "reasoning_content": "Check the order.",
    "thinking_blocks": THINKING,
    "reasoning_items": [{"id": "rs_1", "encrypted_content": "gAAAA"}],
    "provider_specific_fields": {"reasoning_details": DETAILS, "thought_signatures": ["sig-gemini"]},
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "provider_specific_fields": {"thought_signature": "sig-gemini"},
            "function": {
                "name": "lookup_order",
                "arguments": '{"order_id": "A-17"}',
                "provider_specific_fields": {"thought_signature": "sig-gemini"},
            },
        }
    ],
}
MESSAGES = [
    {"role": "user", "content": "Status of A-17?"},
    ASSISTANT,
    {"role": "tool", "tool_call_id": "call_1", "content": "shipped"},
]


def _sent_assistant(provider: str, model: str = "model") -> dict:
    connection = LLMConnection(
        model_config={"provider": provider, "model": model}, api_key="fake-key"
    )
    return connection._completion_params(MESSAGES)["messages"][1]


def test_anthropic_receives_its_thinking_blocks():
    sent = _sent_assistant("anthropic")

    assert sent["thinking_blocks"] == THINKING
    assert "provider_specific_fields" not in sent
    assert "provider_specific_fields" not in sent["tool_calls"][0]


def test_gemini_receives_its_signatures():
    sent = _sent_assistant("gemini")

    call = sent["tool_calls"][0]
    assert call["provider_specific_fields"] == {"thought_signature": "sig-gemini"}
    assert call["function"]["provider_specific_fields"] == {"thought_signature": "sig-gemini"}
    assert sent["provider_specific_fields"] == ASSISTANT["provider_specific_fields"]
    assert "thinking_blocks" not in sent


def test_openrouter_receives_reasoning_details_at_the_top_level():
    sent = _sent_assistant("openrouter", "anthropic/claude-sonnet-4.5")

    assert sent["reasoning_details"] == DETAILS
    assert "provider_specific_fields" not in sent
    assert "thinking_blocks" not in sent
    assert "provider_specific_fields" not in sent["tool_calls"][0]


@pytest.mark.parametrize("provider", ["openai", "groq", "deepseek", "mistral", "azure"])
def test_other_providers_receive_exactly_what_they_did_before(provider):
    sent = _sent_assistant(provider)

    assert set(sent) == {"role", "content", "reasoning_content", "tool_calls"}
    assert sent["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup_order", "arguments": '{"order_id": "A-17"}'},
        }
    ]


def test_building_a_request_never_changes_the_stored_message():
    before = repr(ASSISTANT)
    for provider in ("anthropic", "gemini", "openrouter", "openai"):
        _sent_assistant(provider)

    assert repr(ASSISTANT) == before
