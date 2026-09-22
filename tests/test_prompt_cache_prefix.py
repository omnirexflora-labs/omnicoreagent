"""A session's next run must resend earlier context byte for byte.

Provider prompt caches match on an exact request prefix, so any earlier
message that is replayed differently from how it was first sent makes every
later message uncached.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _plain(messages):
    return [
        json.loads(
            json.dumps(
                message,
                default=lambda value: value.model_dump()
                if hasattr(value, "model_dump")
                else str(value),
            )
        )
        for message in messages
    ]


class RecordingModel:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.requests.append(_plain(messages))
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        if len(self.requests) % 2:
            call = ToolRequest(f"call_{len(self.requests)}", "lookup", '{"key": "a"}')
            return ModelTurn(tool_calls=(call,), finish_reason="tool_calls", usage=usage)
        return ModelTurn(content=f"answer {len(self.requests)}", finish_reason="stop", usage=usage)


async def _agent():
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key}

    agent = OmniCoreAgent(
        name="cache-probe",
        system_instruction="You probe prompt caching.",
        model_config=_MODEL,
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = RecordingModel()
    return agent


@pytest.mark.asyncio
async def test_next_run_resends_the_earlier_conversation_unchanged():
    agent = await _agent()
    model = agent.llm_connection

    await agent.run("first question", session_id="cache")
    # A later second so a re-generated timestamp would differ.
    await asyncio.sleep(1.1)
    await agent.run("second question", session_id="cache")

    earlier = model.requests[1]
    later = model.requests[2]
    assert later[: len(earlier)] == earlier
    assert later[1]["content"].startswith("[CURRENT_DATETIME: ")
    assert later[1]["content"].endswith("\n\nfirst question")


@pytest.mark.asyncio
async def test_stored_history_keeps_the_query_without_the_runtime_prefix():
    agent = await _agent()

    await agent.run("first question", session_id="clean")

    history = await agent.get_session_history(session_id="clean")
    user = [message for message in history if message["role"] == "user"]
    assert user[0]["content"] == "first question"
