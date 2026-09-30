"""Closing an SSE stream cancels its run, whatever the request timeout.

The 0.5.0rc4 gate: the run was cancelled only when the server closed the
stream's generator, which through the middleware did not reliably happen.
With the request timeout off, a long answer cut off after 2.5 s stayed
`running` for over five minutes, its heartbeat renewing, until the server
stopped.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.serve.sse import run_agent_stream
from test_execute_tool import _MODEL


class Hangs:
    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        await asyncio.sleep(3600)

    async def llm_stream(self, messages, tools=None, **kwargs):
        # A stream run (on_event) uses this: a long answer still coming.
        await asyncio.sleep(3600)
        yield {"type": "turn_complete"}


@pytest.mark.asyncio
async def test_a_closed_stream_cancels_its_run_with_no_request_timeout():
    agent = OmniCoreAgent(name="streamer", system_instruction="x", model_config=_MODEL,
                          agent_config={"guardrail_mode": "off", "enable_workspace_files": False})
    await agent.initialize()
    agent.llm_connection = Hangs()
    gone_at = time.monotonic() + 1.0

    async def is_disconnected():
        return time.monotonic() > gone_at

    started = time.monotonic()
    async for _ in run_agent_stream(agent, "write an essay", "cut", timeout_seconds=None,
                                    is_disconnected=is_disconnected):
        pass
    assert time.monotonic() - started < 10, "the stream kept going after the client left"

    for _ in range(50):
        runs = await agent.list_runs(session_id="cut")
        if runs and runs[0]["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert [r["status"] for r in runs] == ["cancelled"], [(r["status"], r.get("error")) for r in runs]
    await agent.cleanup()
