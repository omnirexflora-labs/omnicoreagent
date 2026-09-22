"""Audit: the model client's import cost is paid at startup, not by a request.

``import litellm`` costs about nine seconds of CPU on the audit machine, and
it is imported lazily, so the first real request of a process paid for it.
An agent's ``initialize()`` stays as light as it was (a guard elsewhere holds
that); a server warms the client while it starts, in a thread, so no request
ever pays.
"""

from __future__ import annotations

import threading

import pytest

from omnicoreagent.core import llm as llm_module
from omnicoreagent.core.llm import LLMConnection

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


@pytest.fixture
def stand_in_litellm(monkeypatch):
    """Loading the real client here would cost the suite nine seconds a run."""
    loaded = {"count": 0, "threads": set()}

    def fake_get_litellm():
        loaded["count"] += 1
        loaded["threads"].add(threading.current_thread().name)
        return object()

    monkeypatch.setattr(llm_module, "_get_litellm", fake_get_litellm)
    return loaded


@pytest.mark.asyncio
async def test_warming_up_loads_the_client_off_the_event_loop(stand_in_litellm):
    connection = LLMConnection(MODEL)

    await connection.warm_up()

    assert stand_in_litellm["count"] == 1
    assert threading.current_thread().name not in stand_in_litellm["threads"], (
        "the client was loaded on the event loop's thread, which is what blocks a request"
    )


@pytest.mark.asyncio
async def test_warming_up_twice_loads_the_client_once(stand_in_litellm):
    connection = LLMConnection(MODEL)

    await connection.warm_up()
    await connection.warm_up()

    assert stand_in_litellm["count"] == 1


@pytest.mark.asyncio
async def test_a_failed_warm_up_does_not_fail_startup(monkeypatch):
    def broken():
        raise RuntimeError("no provider client here")

    monkeypatch.setattr(llm_module, "_get_litellm", broken)
    connection = LLMConnection(MODEL)

    await connection.warm_up()  # the request that needs it will report the error


def test_serving_an_agent_warms_the_model_client_at_startup(tmp_path, monkeypatch):
    """The server, not the first request, pays for the client."""
    import asyncio

    from fastapi.testclient import TestClient

    from omnicoreagent import OmniServe, OmniServeConfig
    from test_budget_enforcement import _agent

    warmed = []

    async def record_warm_up(self):
        warmed.append(type(self).__name__)

    monkeypatch.setattr(LLMConnection, "warm_up", record_warm_up)
    agent = asyncio.run(_agent(None))
    agent.llm_connection = LLMConnection(MODEL)
    server = OmniServe(agent, OmniServeConfig(request_timeout=10))

    with TestClient(server.app):
        pass

    # The server warms it as it starts and the background worker warms the
    # agents it serves; the same agent may be both. warm_up() itself absorbs
    # the repeat (tested above); this stand-in does not, so it counts each.
    assert warmed and warmed[0] == "LLMConnection"
