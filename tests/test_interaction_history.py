import pytest

from omnicoreagent.core.context_manager import AgentLoopContextManager
from omnicoreagent.core.interaction_history import (
    context_evidence,
    render_message,
    split_recent,
    stable_message_digest,
)
from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
)
from omnicoreagent.core.summarizer.summarizer_engine import (
    prepare_history_sliding_window,
    prepare_history_token_budget,
)
from omnicoreagent.core.summarizer.tokenizer import count_message_tokens
from omnicoreagent.core.agents.message_history import AgentMessageHistoryLoader
from omnicoreagent.core.agents.session_state import AgentSessionStateStore


def batch():
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "first",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"value":"001"}'},
                },
                {
                    "id": "second",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"value":"002"}'},
                },
            ],
        },
        {"role": "tool", "content": "result one", "tool_call_id": "first"},
        {"role": "tool", "content": "result two", "tool_call_id": "second"},
    ]


@pytest.mark.asyncio
async def test_active_recent_floor_expands_to_complete_tool_group():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
    ] + batch()
    manager = AgentLoopContextManager({"enabled": True, "preserve_recent": 2})
    managed = await manager.manage_context(messages)
    assert managed == [messages[0]] + batch()


@pytest.mark.asyncio
async def test_stored_sliding_window_drops_whole_group_when_it_does_not_fit():
    messages = (
        [{"role": "user", "content": "old"}]
        + batch()
        + [{"role": "assistant", "content": "done"}]
    )
    managed, _ = await prepare_history_sliding_window(messages, window_size=3)
    assert managed == [messages[-1]]
    assert split_recent(messages, 0) == (messages, [])


@pytest.mark.asyncio
async def test_token_budget_never_keeps_orphan_tool_results():
    messages = batch() + [{"role": "assistant", "content": "done"}]
    managed, _ = await prepare_history_token_budget(messages, max_tokens=12)
    assert managed == [messages[-1]]
    assert count_message_tokens(batch()) > count_message_tokens(
        [{"role": "assistant", "content": "result one result two"}]
    )


def test_summary_rendering_includes_calls_and_exact_arguments():
    text = render_message(batch()[0])
    assert "echo" in text
    assert "001" in text
    assert "first" in text


def test_context_digest_uses_privacy_safe_representation():
    recorder = TelemetryRecorder(
        store=InMemoryTelemetryStore(),
        config=TelemetryConfig(redact_keys=["secret"]),
        privacy_filter=PrivacyFilter(PrivacyConfig()),
    )
    message = {
        "role": "user",
        "content": "Contact alice@example.com",
        "metadata": {"secret": "do-not-hash"},
    }
    permitted = recorder.canonicalize_for_digest(message)

    assert permitted["content"] == "Contact [REDACTED_EMAIL]"
    assert permitted["metadata"]["secret"] == "[REDACTED]"
    assert stable_message_digest(message, canonicalizer=recorder.canonicalize_for_digest) == stable_message_digest(
        permitted
    )
    evidence = context_evidence(
        [message],
        canonicalizer=recorder.canonicalize_for_digest,
    )
    assert evidence["message_digests"][0] == stable_message_digest(permitted)


@pytest.mark.asyncio
async def test_unmarked_historical_xml_is_retained_as_task_data():
    content = "<observations><record>Legitimate XML document</record></observations>"

    async def history(**kwargs):
        return [
            {"role": "user", "content": content, "metadata": {"agent_name": "agent"}}
        ]

    state = AgentSessionStateStore("agent").get("session", False)
    await AgentMessageHistoryLoader("agent").load(
        message_history=history, session_id="session", session_state=state
    )
    assert state.messages[0].content == content
