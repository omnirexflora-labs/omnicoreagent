"""Continuation data survives stored history, and privacy never corrupts it.

Providers are the fakes in ``tests/fixtures/continuation_providers.py``: each
rejects a request whose continuation data for an earlier tool call was changed
or dropped, so a second run in the same session proves the stored history kept
it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from continuation_providers import ANSWER, ContinuationProviders  # noqa: E402

from omnicoreagent.core.memory_store.memory_router import MemoryRouter  # noqa: E402
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest  # noqa: E402
from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter  # noqa: E402
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.token_usage import Usage  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402

PROVIDERS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-3-pro-preview",
    "openrouter": "anthropic/claude-sonnet-4.5",
}
# Base64 signatures contain "/" and "+", so digit runs inside them are
# standalone tokens to a PII pattern.
TRICKY_SIGNATURE = "EqQBCkYI/4111111111111111/+14155550100+Zm9v/2026-09-19=="


@pytest.fixture
def providers(monkeypatch):
    fake = ContinuationProviders()
    monkeypatch.setenv("ANTHROPIC_API_BASE", fake.base)
    monkeypatch.setenv("GEMINI_API_BASE", fake.base)
    monkeypatch.setenv("OPENROUTER_API_BASE", f"{fake.base}/api/v1")
    yield fake
    fake.close()


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup_order", description="Look up an order's status.")
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    return tools


def _memory(kind: str, tmp_path, monkeypatch):
    if kind == "sql":
        from omnicoreagent.core.memory_store.sql_db_memory import close_all_sql_managers

        close_all_sql_managers()
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory.db'}")
        return MemoryRouter("sql")
    return MemoryRouter("in_memory")


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", ["in_memory", "sql"])
@pytest.mark.parametrize("provider", sorted(PROVIDERS))
async def test_a_second_run_resends_the_stored_continuation_data(
    providers, provider, memory, tmp_path, monkeypatch
):
    agent = OmniCoreAgent(
        name=f"history-{provider}",
        system_instruction="Use lookup_order, then answer.",
        model_config={"provider": provider, "model": PROVIDERS[provider], "api_key": "fake-key"},
        local_tools=_tools(),
        memory_router=_memory(memory, tmp_path, monkeypatch),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "max_steps": 4},
    )
    await agent.initialize()

    first = await agent.run("What is the status of order A-17?", session_id="history")
    # The next run starts from stored history, not from the first run's memory.
    second = await agent.run("And order A-17 again?", session_id="history")

    assert providers.rejections == [], providers.rejections
    assert ANSWER in first["response"] and ANSWER in second["response"]


def test_opaque_values_are_never_pattern_scanned():
    privacy = PrivacyFilter()
    message = {
        "role": "assistant",
        "thinking_blocks": [
            {"type": "thinking", "thinking": "Look up the order.", "signature": TRICKY_SIGNATURE},
            {"type": "redacted_thinking", "data": TRICKY_SIGNATURE},
        ],
        "reasoning_items": [{"id": "rs_4111111111111111", "encrypted_content": TRICKY_SIGNATURE}],
        "tool_calls": [
            {
                "id": f"call_1__thought__{TRICKY_SIGNATURE}",
                "type": "function",
                "provider_specific_fields": {"thought_signature": TRICKY_SIGNATURE},
                "function": {"name": "lookup_order", "arguments": "{}"},
            }
        ],
        "provider_specific_fields": {
            "thought_signatures": [TRICKY_SIGNATURE],
            "reasoning_details": [
                {"type": "reasoning.encrypted", "data": TRICKY_SIGNATURE, "signature": TRICKY_SIGNATURE}
            ],
        },
    }

    assert privacy.redact(message, boundary="memory") == message
    # Free text is still redacted.
    assert "4111111111111111" not in privacy.redact(
        {"content": "card 4111 1111 1111 1111"}, boundary="memory"
    )["content"]


def test_thinking_that_privacy_must_change_is_not_stored_corrupted():
    from omnicoreagent.core.runtime.omnicore_agent import _without_changed_continuation

    thinking = [{"type": "thinking", "thinking": "Email ada@example.com the result.", "signature": "sig"}]
    safe = [{"type": "thinking", "thinking": "Look up the order.", "signature": "sig"}]

    def stored(blocks):
        metadata = {
            "has_tool_calls": True,
            "model_message": {"role": "assistant", "content": None, "thinking_blocks": blocks},
        }
        return _without_changed_continuation(
            metadata,
            PrivacyFilter(PrivacyConfig(redact_memory=True)).redact(metadata, boundary="memory"),
        )

    changed = stored(thinking)
    assert "ada@example.com" not in json.dumps(changed)
    assert "thinking_blocks" not in changed["model_message"]
    assert changed["continuation_dropped"] == ["thinking_blocks"]
    # Thinking without personal data is stored exactly, signature included.
    unchanged = stored(safe)
    assert unchanged["model_message"]["thinking_blocks"] == safe
    assert "continuation_dropped" not in unchanged


class _OneCallModel:
    def __init__(self):
        self.turn = 0

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.turn += 1
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        if self.turn == 1:
            call = ToolRequest("call_1", "lookup_order", '{"order_id": "A-17-secret"}')
            return ModelTurn(tool_calls=(call,), finish_reason="tool_calls", usage=usage)
        return ModelTurn(content="done", finish_reason="stop", usage=usage)


@pytest.mark.asyncio
async def test_history_keeps_real_tool_arguments_while_the_trace_redacts_them():
    agent = OmniCoreAgent(
        name="governed-history",
        system_instruction="x",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "fake-key"},
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {
                "enabled": True,
                "policy": {
                    "name": "history-policy",
                    "mode": "strict",
                    "rules": {"allow": [{"rule_id": "allow_local", "capability": "tool.local.call"}]},
                },
            },
        },
    )
    await agent.initialize()
    agent.llm_connection = _OneCallModel()

    result = await agent.run("go", session_id="governed")

    stored = await agent.memory_router.get_messages("governed", agent.agent.agent_name)
    [call] = next(m for m in stored if m["role"] == "assistant" and m["metadata"].get("has_tool_calls"))[
        "metadata"
    ]["model_message"]["tool_calls"]
    # The model sees its own past call as it made it.
    assert json.loads(call["function"]["arguments"]) == {"order_id": "A-17-secret"}
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert span.input["tool_args"] == {"order_id": "[REDACTED]"}
