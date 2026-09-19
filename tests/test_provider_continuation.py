"""Provider continuation data survives a real OmniCoreAgent tool loop.

The providers are local fakes speaking each provider's real wire format
(``tests/fixtures/continuation_providers.py``), reached through LiteLLM's real
Anthropic, Gemini, and OpenRouter code by their base-URL variables. Like the
real APIs, each rejects the second model call when the continuation data it
issued with the first is missing or changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from continuation_providers import ANSWER, ContinuationProviders  # noqa: E402

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402

PROVIDERS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-3-pro-preview",
    "openrouter": "anthropic/claude-sonnet-4.5",
}


@pytest.fixture
def providers(monkeypatch):
    fake = ContinuationProviders()
    monkeypatch.setenv("ANTHROPIC_API_BASE", fake.base)
    monkeypatch.setenv("GEMINI_API_BASE", fake.base)
    monkeypatch.setenv("OPENROUTER_API_BASE", f"{fake.base}/api/v1")
    yield fake
    fake.close()


async def _agent(provider: str) -> OmniCoreAgent:
    tools = ToolRegistry()

    @tools.register_tool("lookup_order", description="Look up an order's status.")
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    agent = OmniCoreAgent(
        name=f"continuation-{provider}",
        system_instruction="Use lookup_order, then answer.",
        model_config={"provider": provider, "model": PROVIDERS[provider], "api_key": "fake-key"},
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "max_steps": 4},
    )
    await agent.initialize()
    return agent


async def _answer(agent: OmniCoreAgent, mode: str) -> str:
    query = "What is the status of order A-17?"
    if mode == "run":
        result = await agent.run(query, session_id=f"continuation-{mode}")
        return result["response"]
    text = []
    async for event in agent.stream(query, session_id=f"continuation-{mode}"):
        delta = getattr(event, "text", None) or (event.get("text") if isinstance(event, dict) else None)
        if delta:
            text.append(delta)
    return "".join(text)


# Proven in P1 of the provider continuation plan: the thinking data is dropped,
# so the second model call is rejected. Gemini passes only because LiteLLM also
# encodes its signature in the tool-call ID, which OmniCoreAgent preserves.
DROPPED_TODAY = {"anthropic", "openrouter"}


def _case(provider: str, mode: str):
    marks = (
        [pytest.mark.xfail(strict=True, reason="continuation data dropped; plan units P2 to P4")]
        if provider in DROPPED_TODAY
        else []
    )
    return pytest.param(provider, mode, marks=marks, id=f"{provider}-{mode}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "mode"),
    [_case(provider, mode) for provider in sorted(PROVIDERS) for mode in ("run", "stream")],
)
async def test_a_two_step_tool_loop_keeps_the_providers_continuation_data(providers, provider, mode):
    agent = await _agent(provider)

    answer = await _answer(agent, mode)

    assert providers.rejections == [], providers.rejections
    assert ANSWER in answer, answer
