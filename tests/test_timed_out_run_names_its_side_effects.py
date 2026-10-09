"""A run that ends after side effects says what already happened.

The support desk chaos run (2026-10-07), provider-hang rounds: a refund was
issued, then the run hit its deadline on the next model call. The client got
`status: timeout` and "The run's deadline passed", and nothing about the
refund. A client that started a new run instead of resuming could refund
twice. The record, its error and every OmniServe response for the run now list
the non-idempotent calls that completed or have an unknown outcome.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.deadline import run_with_timeout
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.serve import OmniServe, OmniServeConfig

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}
REFUND = [{"tool_name": "refund", "tool_call_id": "c1", "outcome": "success"}]


class RefundThenHang:
    """Asks for a refund, then hangs on the next model call (the first time
    only), as a provider that stopped answering; a later call answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=(ToolRequest("c1", "refund", '{"order_id": "2001"}'),),
                finish_reason="tool_calls",
            )
        if self.calls == 2:
            await asyncio.sleep(30)
        return "refunded"

    async def llm_stream(self, messages, tools=None):
        yield {"type": "turn_complete", "turn": await self.llm_call(messages, tools)}


def _agent(ledger: list, model: RefundThenHang | None = None) -> OmniCoreAgent:
    tools = ToolRegistry()

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order_id: str) -> dict:
        ledger.append(order_id)
        return {"issued": True}

    @tools.register_tool("lookup", description="Looks an order up.", idempotent=True)
    def lookup(order_id: str) -> dict:
        return {"found": True}

    agent = OmniCoreAgent(
        name="desk", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    agent.llm_connection = model or RefundThenHang()
    return agent


@pytest.mark.asyncio
async def test_a_run_that_timed_out_after_a_refund_lists_it_and_resumes():
    ledger: list = []
    agent = _agent(ledger)
    await agent.initialize()
    agent.llm_connection = RefundThenHang()

    with pytest.raises(asyncio.TimeoutError):
        await run_with_timeout(agent.run("refund 2001", session_id="s", run_id="run_t1"), 1.5)

    record = await agent.get_run("run_t1")
    assert record["status"] == "timeout"
    assert record["side_effects"] == REFUND
    # In the error too, so a client that reads only the error sees it.
    assert record["error"]["type"] == "TimeoutError"
    assert record["error"]["side_effects"] == REFUND

    # Resuming continues the run: the refund is not made again.
    result = await agent.resume("run_t1")
    assert result["status"] == "success", result
    assert ledger == ["2001"]
    finished = await agent.get_run("run_t1")
    assert finished["status"] == "completed"
    assert not finished.get("side_effects") and finished["error"] is None
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_failed_run_lists_unknown_calls_and_leaves_out_reads_and_clean_runs():
    ledger: list = []
    started = threading.Event()
    agent = _agent(ledger)
    await agent.initialize()

    class Fails(RefundThenHang):
        async def llm_call(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ModelTurn(
                    tool_calls=(
                        ToolRequest("c1", "refund", '{"order_id": "2001"}'),
                        ToolRequest("c2", "lookup", '{"order_id": "2001"}'),
                    ),
                    finish_reason="tool_calls",
                )
            started.set()
            raise RuntimeError("the model client broke")

    agent.llm_connection = Fails()
    try:
        await agent.run("refund 2001", session_id="s", run_id="run_f1")
    except Exception:
        pass
    record = await agent.get_run("run_f1")
    assert record["status"] == "failed"
    # The idempotent read is not a side effect worth naming.
    assert record["side_effects"] == REFUND
    assert record["error"]["side_effects"] == REFUND

    await agent.run("hello", session_id="s2", run_id="run_ok")
    assert (await agent.get_run("run_ok")).get("side_effects") in (None, [])
    await agent.cleanup()


def _server(tmp_path, monkeypatch, ledger):
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path))
    agent = _agent(ledger)
    client = TestClient(OmniServe(agent=agent, config=OmniServeConfig(request_timeout=2)).app)
    asyncio.run(agent.initialize())
    agent.llm_connection = RefundThenHang()
    return agent, client


def test_the_sync_route_and_the_run_record_name_the_refund(tmp_path, monkeypatch):
    ledger: list = []
    _, client = _server(tmp_path, monkeypatch, ledger)

    response = client.post("/run/sync", json={"query": "refund 2001"})

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["side_effects"] == REFUND, detail
    run = client.get(f"/runs/{detail['run_id']}").json()
    assert run["status"] == "timeout"
    assert run["side_effects"] == REFUND
    assert run["error"]["side_effects"] == REFUND

    # The documented way on: resume the same run.
    resumed = client.post(f"/runs/{detail['run_id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert ledger == ["2001"]


def test_the_stream_names_the_refund_when_it_times_out(tmp_path, monkeypatch):
    ledger: list = []
    _, client = _server(tmp_path, monkeypatch, ledger)

    text = client.post("/run", json={"query": "refund 2001"}).text

    errors = [
        json.loads(block.split("data: ", 1)[1])
        for block in text.split("\n\n")
        if block.startswith("event: error")
    ]
    assert errors, text
    assert errors[-1]["side_effects"] == REFUND, errors[-1]
    assert ledger == ["2001"]
