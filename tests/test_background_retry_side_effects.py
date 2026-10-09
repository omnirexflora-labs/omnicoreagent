"""A background retry must not repeat a side effect.

A task whose `retry_on` includes `timeout` or `exception` started a NEW attempt
on a retry, and a new attempt wipes the run's tool calls and context: a
background refund job that timed out after issuing the refund would issue it
again. A timed-out run now resumes (completed calls are not run again); a
failed run is retried from the start only if it did nothing that cannot be
repeated, and otherwise waits for a person, with its `side_effects` listed.
"""

from __future__ import annotations

import asyncio

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.background import BackgroundAgentManager, RunStatus
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.manager import Workspace

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class Script:
    """Turn 1 asks for the calls; turn 2 hangs or fails the first time it is
    reached; every later turn answers."""

    def __init__(self, calls, second):
        self.calls = 0
        self.first_calls = calls
        self.second = second
        self.broken = False

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(tool_calls=self.first_calls, finish_reason="tool_calls")
        if not self.broken:
            self.broken = True
            if self.second == "hang":
                await asyncio.sleep(30)
            raise RuntimeError("the model client broke")
        return "done"

    async def llm_stream(self, messages, tools=None):
        yield {"type": "turn_complete", "turn": await self.llm_call(messages, tools)}


def _agent(ledger, reads):
    tools = ToolRegistry()

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order_id: str) -> dict:
        ledger.append(order_id)
        return {"issued": True}

    @tools.register_tool("lookup", description="Looks an order up.", idempotent=True)
    def lookup(order_id: str) -> dict:
        reads.append(order_id)
        return {"found": True}

    return OmniCoreAgent(
        name="desk", system_instruction="x", model_config=_MODEL, local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )


async def _run(tmp_path, calls, second, *, timeout_seconds=None):
    ledger, reads = [], []
    agent = _agent(ledger, reads)
    await agent.initialize()
    agent.llm_connection = Script(calls, second)
    workspace = Workspace.from_config(
        {"workspace_backend": "local", "workspace_dir": str(tmp_path / "bg")}
    ).ensure()
    manager = BackgroundAgentManager(task_store="in_memory", workspace=workspace, lease_seconds=30)
    await manager.register_agent("desk", agent)
    await manager.register_task(
        task_id="job", agent_id="desk", query="refund 2001", schedule={"type": "manual"},
        timeout_seconds=timeout_seconds,
        retry_policy={"max_retries": 1, "initial_delay_seconds": 0},
    )
    run = await manager.run_now("job")
    finished = await manager.run_until_terminal(run.run_id, timeout_seconds=30)
    return manager, agent, finished, ledger, reads


REFUND = (ToolRequest("c1", "refund", '{"order_id": "2001"}'),)
READ = (ToolRequest("c1", "lookup", '{"order_id": "2001"}'),)


@pytest.mark.asyncio
async def test_a_timed_out_refund_job_resumes_on_retry_and_refunds_once(tmp_path):
    manager, agent, finished, ledger, _ = await _run(tmp_path, REFUND, "hang", timeout_seconds=2)

    assert finished.status == RunStatus.COMPLETED, finished.error
    assert ledger == ["2001"]
    record = await agent.get_run(finished.run_id)
    assert record["status"] == "completed"
    refunds = [c for c in record["tool_calls"] if c["tool_name"] == "refund"]
    assert len(refunds) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_a_failed_refund_job_is_not_retried_and_lists_its_side_effects(tmp_path):
    manager, agent, finished, ledger, _ = await _run(tmp_path, REFUND, "fail")

    assert finished.status == RunStatus.FAILED
    assert ledger == ["2001"]
    attempts = await manager.task_store.list_attempts(finished.run_id)
    assert len(attempts) == 1
    effects = [{"tool_name": "refund", "tool_call_id": "c1", "outcome": "success"}]
    assert finished.metadata["side_effects"] == effects
    assert "side effects" in finished.error and "refund" in finished.error
    events = [e["event"] for e in await manager.get_run_events(finished.run_id)]
    assert "background_run_retrying" not in events
    await manager.shutdown()


@pytest.mark.asyncio
async def test_a_failed_job_that_only_read_is_retried_as_before(tmp_path):
    manager, _, finished, ledger, reads = await _run(tmp_path, READ, "fail")

    assert finished.status == RunStatus.COMPLETED, finished.error
    assert ledger == []
    assert len(await manager.task_store.list_attempts(finished.run_id)) == 2
    assert "side_effects" not in finished.metadata
    await manager.shutdown()
