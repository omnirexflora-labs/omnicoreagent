"""Context management and telemetry handle provider continuation data.

Compression never changes a kept turn's continuation data, the summarizer
never receives opaque values, and traces record that continuation data was
present (counts and a digest) without ever storing a signature or encrypted
value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from continuation_providers import (  # noqa: E402
    GEMINI_SIGNATURE,
    REDACTED,
    SIGNATURE,
    THINKING,
    ContinuationProviders,
)

from omnicoreagent.core.interaction_history import render_message  # noqa: E402
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402

OPAQUE = (SIGNATURE, REDACTED, GEMINI_SIGNATURE)
ASSISTANT = {
    "role": "assistant",
    "content": None,
    "thinking_blocks": [
        {"type": "thinking", "thinking": THINKING, "signature": SIGNATURE},
        {"type": "redacted_thinking", "data": REDACTED},
    ],
    "tool_calls": [
        {
            "id": f"call_1__thought__{GEMINI_SIGNATURE}",
            "type": "function",
            "provider_specific_fields": {"thought_signature": GEMINI_SIGNATURE},
            "function": {"name": "lookup_order", "arguments": '{"order_id": "A-17"}'},
        }
    ],
}


def test_the_summarizer_never_receives_opaque_values():
    rendered = render_message(ASSISTANT) + render_message(
        {"role": "tool", "tool_call_id": ASSISTANT["tool_calls"][0]["id"], "content": "shipped"}
    )

    for value in OPAQUE:
        assert value not in rendered
    assert "lookup_order" in rendered and "A-17" in rendered


@pytest.mark.asyncio
async def test_compression_keeps_recent_turns_and_their_continuation_data_unchanged():
    from copy import deepcopy

    from omnicoreagent.core.context_manager import AgentLoopContextManager

    manager = AgentLoopContextManager(
        {"enabled": True, "mode": "sliding_window", "value": 3, "strategy": "truncate", "preserve_recent": 3}
    )
    tool_result = {"role": "tool", "tool_call_id": ASSISTANT["tool_calls"][0]["id"], "content": "shipped"}
    older = [{"role": "user", "content": f"old {i}"} for i in range(6)]
    messages = [{"role": "system", "content": "s"}, *older, {"role": "user", "content": "now"}, deepcopy(ASSISTANT), tool_result]

    managed = await manager.manage_context(messages)

    kept = next(m for m in managed if isinstance(m, dict) and m.get("tool_calls"))
    assert kept == ASSISTANT


@pytest.fixture
def providers(monkeypatch):
    fake = ContinuationProviders()
    monkeypatch.setenv("ANTHROPIC_API_BASE", fake.base)
    monkeypatch.setenv("GEMINI_API_BASE", fake.base)
    monkeypatch.setenv("OPENROUTER_API_BASE", f"{fake.base}/api/v1")
    yield fake
    fake.close()


async def _traced_run(provider: str, model: str, capture: str | None):
    tools = ToolRegistry()

    @tools.register_tool("lookup_order", description="Look up an order's status.")
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    agent = OmniCoreAgent(
        name=f"traced-{provider}",
        system_instruction="Use lookup_order, then answer.",
        model_config={"provider": provider, "model": model, "api_key": "fake-key"},
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "max_steps": 4},
        telemetry_config={"capture": capture} if capture else None,
    )
    await agent.initialize()
    result = await agent.run("What is the status of order A-17?", session_id="traced")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    return agent, result, trace


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["full", None])
@pytest.mark.parametrize(
    ("provider", "model"),
    [("anthropic", "claude-sonnet-4-5"), ("gemini", "gemini-3-pro-preview")],
)
async def test_traces_record_continuation_without_storing_opaque_values(
    providers, provider, model, capture
):
    agent, result, trace = await _traced_run(provider, model, capture)

    dump = json.dumps(trace.model_dump(), default=str)
    for value in OPAQUE:
        assert value not in dump, f"{value[:12]} stored in the trace"
    [first] = [
        e for e in trace.events if e.event_type == "model_response"
    ][:1]
    continuation = first.metadata["model_call"]["continuation"]
    if provider == "anthropic":
        assert (continuation["thinking_blocks"], continuation["redacted_thinking"]) == (1, 1)
    else:
        assert continuation["tool_call_signatures"] == 1
    assert len(continuation["digest"]) == 16
    trajectory = await agent.get_trajectory(result["trace_id"])
    [call] = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "success" and call["observation"]
    if capture == "full" and provider == "anthropic":
        # The thinking text is kept under full capture; its signature is not.
        assert THINKING in dump
    if capture is None:
        assert THINKING not in dump


@pytest.mark.asyncio
async def test_a_continuation_run_exports_valid_portable_evidence(providers):
    from omnicoreagent.core.telemetry import OmniCoreEvidenceAdapter, validate_portable_evidence_document

    _, _, trace = await _traced_run("anthropic", "claude-sonnet-4-5", "full")

    validate_portable_evidence_document(OmniCoreEvidenceAdapter().import_trace(trace).model_dump())
