"""`privacy_config["redact_model_io"]` keeps personal data from the model provider.

Found writing the privacy page (D6): the setting was validated and documented
but nothing applied it, so an agent configured with it still sent a user's
email address to the provider. What the provider is sent is now redacted at
the model boundary, on every call (turns and summaries alike); the default
still sends the real text.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}
MESSAGES = [
    {"role": "user", "content": "Email jane@example.com about order 42."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"email": "jane@example.com"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "jane@example.com: 2 orders"},
]


async def _agent(tmp_path, monkeypatch, **privacy) -> OmniCoreAgent:
    monkeypatch.chdir(tmp_path)
    agent = OmniCoreAgent(
        name="privacy",
        system_instruction="x",
        model_config=MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "privacy_config": privacy,
        },
    )
    await agent.initialize()
    return agent


@pytest.mark.asyncio
async def test_the_provider_is_sent_redacted_messages_when_asked(tmp_path, monkeypatch):
    agent = await _agent(tmp_path, monkeypatch, redact_model_io=True)

    sent = agent.llm_connection._completion_params(MESSAGES)["messages"]

    assert "jane@example.com" not in str(sent)
    assert "[REDACTED_EMAIL]" in sent[0]["content"]
    assert sent[2]["tool_call_id"] == "call_1"  # identifiers are left alone
    assert "order 42" in sent[0]["content"]


@pytest.mark.asyncio
async def test_by_default_the_model_sees_the_real_text(tmp_path, monkeypatch):
    agent = await _agent(tmp_path, monkeypatch)

    sent = agent.llm_connection._completion_params(MESSAGES)["messages"]

    assert sent[0]["content"] == "Email jane@example.com about order 42."


@pytest.mark.asyncio
async def test_the_run_header_says_which_boundaries_are_redacted(tmp_path, monkeypatch):
    # The trace is redacted whether or not the model was, so the stranger
    # test could not tell from a run whether redact_model_io was on: only a
    # fingerprint differed. The header says it in words.
    from test_telemetry_tool_record import ScriptedModel

    agent = await _agent(tmp_path, monkeypatch, redact_model_io=True)
    agent.llm_connection = ScriptedModel()
    result = await agent.run("hello")
    header = (await agent.get_trajectory(result["trace_id"]))["harness"]

    assert "model_io" in header["privacy"]["redacted"]
    assert "telemetry" in header["privacy"]["redacted"]
    assert "email" in header["privacy"]["categories"]
