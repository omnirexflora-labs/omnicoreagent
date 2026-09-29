"""R1 (0.5.0rc1 gate): the first model call does not freeze the event loop.

`import litellm` takes seconds of CPU (15-306 s on the loaded gate host). It
ran on the event loop at a process's first model call, so nothing else ran:
the run's heartbeat stalled and a second process took over a live run,
background runs failed as lease expired, MCP and HTTP timers fired. The import
now happens off the loop, and a run warms the model client before its
heartbeat starts, so pricing a call before it is made does not import either.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import omnicoreagent.core.llm as llm_module
from omnicoreagent.core.llm import LLMConnection
from test_llm import make_model_config


def _slow_litellm(calls: list):
    fake = SimpleNamespace()

    async def acompletion(**params):
        return SimpleNamespace(choices=[], usage=None)

    fake.acompletion = acompletion

    def load():
        calls.append(time.monotonic())
        if len(calls) == 1:
            time.sleep(1.0)  # the import's CPU time, holding whatever thread runs it
        return fake

    return load


async def _max_gap_while(coro) -> float:
    gaps, done = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    try:
        await coro
    finally:
        done.set()
        await tick
    # No tick at all means the loop was held for the whole call.
    return max(gaps, default=float("inf"))


@pytest.mark.asyncio
async def test_the_first_model_call_imports_the_client_off_the_event_loop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_module, "_get_litellm", _slow_litellm(calls))
    # As in a fresh process: the client is not loaded yet.
    monkeypatch.delitem(__import__("sys").modules, "litellm", raising=False)
    connection = LLMConnection(make_model_config(), api_key="test-api-key")

    gap = await _max_gap_while(connection.llm_call([{"role": "user", "content": "hi"}]))

    assert calls, "the client was loaded"
    assert gap < 0.5, f"the event loop froze for {gap:.2f} s"


@pytest.mark.asyncio
async def test_a_run_warms_the_client_before_its_heartbeat_starts(monkeypatch):
    from omnicoreagent.core.runs import RunTracker
    from test_run_suspend import RecordingModel
    from test_governed_by_default import _agent

    order: list[str] = []
    agent = await _agent(RecordingModel("hi"))

    async def warm_up():
        order.append("warm_up")

    agent.llm_connection.warm_up = warm_up
    real_start = RunTracker.start

    async def start(self, *args, **kwargs):
        order.append("heartbeat")
        return await real_start(self, *args, **kwargs)

    monkeypatch.setattr(RunTracker, "start", start)
    await agent.run("hi", session_id="warm")

    assert order[:2] == ["warm_up", "heartbeat"], order
    await agent.cleanup()
