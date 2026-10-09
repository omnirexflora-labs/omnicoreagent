"""The fake model provider of the support desk app (``apps/support_desk/fakeprovider``).

Load and fault tests run against it instead of a paid model, so it must be
deterministic, must count what it served, and must fail on command. The agent
reaches it the public way, through ``model_config["base_url"]``, so a real
client (LiteLLM) and the whole runtime are exercised, not a stub.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

APPS = Path(__file__).resolve().parent.parent / "apps" / "support_desk"
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

from fakeprovider.scenario import decide  # noqa: E402
from fakeprovider.server import create_app  # noqa: E402

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402


@contextlib.contextmanager
def running_fake_provider(monkeypatch=None, **env):
    """The fake provider on a free local port, in this process; yields its URL."""
    import os

    saved = {key: os.environ.get(key) for key in env}
    os.environ.update({key: str(value) for key, value in env.items()})
    try:
        app = create_app()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.fixture
def provider():
    with running_fake_provider() as url:
        yield url


def _tools():
    registry = ToolRegistry()

    @registry.register_tool("lookup_order")
    def lookup_order(order_id: str) -> dict:
        """Look up an order by its id."""
        return {"order_id": order_id, "status": "shipped", "total": 42.0}

    @registry.register_tool("search_kb")
    def search_kb(query: str) -> dict:
        """Search the help articles."""
        return {"articles": ["Orders ship in two days."]}

    @registry.register_tool("issue_refund")
    def issue_refund(order_id: str, amount: float) -> dict:
        """Refund an order."""
        return {"order_id": order_id, "refunded": amount}

    return registry


def _agent(url: str, **extra):
    return OmniCoreAgent(
        name="desk-test",
        system_instruction="You help customers with orders.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "fake", "base_url": f"{url}/v1"},
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off", **extra},
    )


def _chat(url, messages, tools=True, **body):
    declared = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
                for name in ("lookup_order", "issue_refund", "search_kb")] if tools else None
    return httpx.post(f"{url}/v1/chat/completions", timeout=30, json={
        "model": "m", "messages": messages, **({"tools": declared} if declared else {}), **body,
    })


# --- the script ----------------------------------------------------------------


def test_the_script_looks_up_the_order_then_searches_then_answers():
    names = {"lookup_order", "issue_refund", "search_kb"}
    messages = [{"role": "user", "content": "Where is order 1042?"}]
    step = decide(messages, names)
    assert (step.tool_name, step.arguments) == ("lookup_order", {"order_id": "1042"})

    messages += [
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"order_id": "1042", "status": "shipped", "total": 42.0})},
    ]
    assert decide(messages, names).tool_name == "search_kb"

    messages += [
        {"role": "assistant", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "search_kb", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "{}"},
    ]
    answer = decide(messages, names)
    assert answer.tool_name is None and "1042" in answer.text and "shipped" in answer.text


def test_a_refund_request_calls_issue_refund_with_the_amount_asked_for():
    names = {"lookup_order", "issue_refund", "search_kb"}
    messages = [
        {"role": "user", "content": "Please refund order 1042, $12.50."},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"data": {"order_id": "1042", "total": 42.0}})},
    ]
    step = decide(messages, names)
    assert (step.tool_name, step.arguments) == ("issue_refund", {"order_id": "1042", "amount": 12.5})


def test_an_earlier_turn_of_the_session_does_not_decide_this_one():
    names = {"lookup_order", "issue_refund", "search_kb"}
    messages = [
        {"role": "user", "content": "Where is order 1042?"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "assistant", "content": "Shipped."},
        {"role": "user", "content": "And order 2001?"},
    ]
    step = decide(messages, names)
    assert (step.tool_name, step.arguments) == ("lookup_order", {"order_id": "2001"})


# --- the HTTP surface ------------------------------------------------------------


def test_a_completion_returns_a_tool_call_and_realistic_usage(provider):
    body = _chat(provider, [{"role": "user", "content": "Where is order 1042?"}]).json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "lookup_order"
    assert json.loads(call["function"]["arguments"]) == {"order_id": "1042"}
    usage = body["usage"]
    assert usage["prompt_tokens"] > 20 and usage["completion_tokens"] > 8
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_a_streamed_completion_arrives_in_chunks_with_usage(provider):
    with httpx.stream("POST", f"{provider}/v1/chat/completions", timeout=30, json={
        "model": "m", "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hello"}],
    }) as response:
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[6:]) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "How can I help you today?"
    assert chunks[-1]["usage"]["total_tokens"] > 0 and chunks[-1]["choices"] == []


def test_stats_count_what_was_served(provider):
    for _ in range(3):
        _chat(provider, [{"role": "user", "content": "Where is order 1042?"}])
    stats = httpx.get(f"{provider}/_stats").json()
    assert stats["requests"] == 3 and stats["steps"] == {"lookup_order": 3}
    assert stats["by_status"] == {"200": 3} and stats["by_path"] == {"/v1/chat/completions": 3}
    httpx.post(f"{provider}/_control", json={"reset": True})
    assert httpx.get(f"{provider}/_stats").json()["requests"] == 0


def test_a_429_carries_retry_after(provider):
    httpx.post(f"{provider}/_control", json={"rate_429": 1.0, "retry_after": 2})
    response = _chat(provider, [{"role": "user", "content": "hi"}])
    assert response.status_code == 429 and response.headers["retry-after"] == "2"
    assert response.json()["error"]["type"] == "rate_limit_exceeded"
    assert httpx.get(f"{provider}/_stats").json()["faults"] == {"429": 1}


def test_a_500_is_an_openai_shaped_error(provider):
    httpx.post(f"{provider}/_control", json={"rate_500": 1.0})
    response = _chat(provider, [{"role": "user", "content": "hi"}])
    assert response.status_code == 500 and response.json()["error"]["type"] == "server_error"


def test_a_timeout_fault_outlasts_the_client(provider):
    httpx.post(f"{provider}/_control", json={"rate_timeout": 1.0, "hang_seconds": 30})
    with pytest.raises(httpx.ReadTimeout):
        httpx.post(f"{provider}/v1/chat/completions", timeout=0.5, json={"model": "m", "messages": []})


def test_latency_is_injected_within_its_range(provider):
    httpx.post(f"{provider}/_control", json={"latency_min": 0.3, "latency_max": 0.5})
    started = time.monotonic()
    _chat(provider, [{"role": "user", "content": "hi"}])
    assert 0.3 <= time.monotonic() - started < 2.0


def test_a_slow_stream_pauses_between_chunks(provider):
    httpx.post(f"{provider}/_control", json={"rate_slow_stream": 1.0, "slow_stream_delay": 0.2})
    started = time.monotonic()
    _chat(provider, [{"role": "user", "content": "hi"}], tools=False, stream=True)
    assert time.monotonic() - started >= 1.0  # six words, a pause before each
    assert httpx.get(f"{provider}/_stats").json()["faults"] == {"slow_stream": 1}


def test_a_bad_control_is_refused(provider):
    assert httpx.post(f"{provider}/_control", json={"nonsense": 1}).status_code == 422


def test_settings_come_from_the_environment():
    with running_fake_provider(FAKE_RATE_500="1") as url:
        assert _chat(url, [{"role": "user", "content": "hi"}]).status_code == 500


def test_the_same_seed_gives_the_same_faults():
    outcomes = []
    for _ in range(2):
        with running_fake_provider(FAKE_SEED="7", FAKE_RATE_500="0.5") as url:
            outcomes.append([_chat(url, [{"role": "user", "content": "hi"}]).status_code for _ in range(8)])
    assert outcomes[0] == outcomes[1] and set(outcomes[0]) == {200, 500}


# --- a real agent against it -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_agent_runs_a_whole_support_chat_through_base_url(provider):
    agent = _agent(provider)
    try:
        result = await agent.run("Where is order 1042?", session_id="alice")
    finally:
        await agent.cleanup()
    assert result["status"] == "success", result
    assert "1042" in result["response"] and "shipped" in result["response"]

    stats = httpx.get(f"{provider}/_stats").json()
    # The client LiteLLM uses for provider "openai" is chat completions.
    assert stats["by_path"] == {"/v1/chat/completions": 3}
    assert stats["steps"] == {"lookup_order": 1, "search_kb": 1, "answer": 1}
    assert result["metric"].requests == 3
    assert result["metric"].request_tokens == stats["prompt_tokens"]


@pytest.mark.asyncio
async def test_a_real_agent_retries_after_a_429_and_a_500(provider):
    # Two faults, then clean: the agent's client must absorb them.
    httpx.post(f"{provider}/_control", json={"rate_429": 1.0, "retry_after": 0})
    agent = _agent(provider)
    try:
        task = asyncio.create_task(agent.run("Where is order 1042?", session_id="bob"))
        await asyncio.sleep(1.5)
        httpx.post(f"{provider}/_control", json={"rate_429": 0.0})
        result = await asyncio.wait_for(task, 60)
    finally:
        await agent.cleanup()
    stats = httpx.get(f"{provider}/_stats").json()
    assert stats["faults"].get("429", 0) >= 1, stats
    assert result["status"] == "success", result


def test_timings_name_the_conversation_and_what_a_429_told_it_to_wait(provider):
    # The chaos harness asks, from these, whether a client waited as long as
    # Retry-After said: it needs the conversation, the status and the wait.
    httpx.post(f"{provider}/_control", json={"rate_429": 1.0, "retry_after": 3})
    _chat(provider, [{"role": "user", "content": "[CURRENT_DATETIME: now] hi ref a"}])
    httpx.post(f"{provider}/_control", json={"rate_429": 0.0})
    _chat(provider, [{"role": "user", "content": "hi ref a"}, {"role": "assistant", "content": "x"}])
    _chat(provider, [{"role": "user", "content": "hi ref b"}])
    rows = httpx.get(f"{provider}/_timings").json()["timings"]
    assert [r["status"] for r in rows] == [429, 200, 200]
    assert rows[0]["retry_after"] == 3 and rows[0]["t_out"] >= rows[0]["t_in"]
    # The clock line the runtime adds does not change the conversation's key.
    assert rows[0]["conv"] == rows[1]["conv"] != rows[2]["conv"]
    later = httpx.get(f"{provider}/_timings", params={"since": rows[2]["t_in"]}).json()["timings"]
    assert len(later) == 1
