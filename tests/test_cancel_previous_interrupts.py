"""`cancel_previous` interrupts the run it replaces.

Found writing Background agents (D7): the previous run was only flagged; it
finished its whole attempt (spending and acting) and was then recorded
cancelled, its work thrown away. The maintainer's decision (2026-09-28): it
interrupts the run, which is recorded cancelled where it stopped.
"""

from __future__ import annotations

import asyncio

import pytest

from omnicoreagent.background import BackgroundAgentManager
from omnicoreagent.background.models import OverlapPolicy, RunStatus
from test_background_agent import FakeAgent, wait_for


class SlowAgent(FakeAgent):
    def __init__(self):
        super().__init__(response="done")
        self.finished: list[str] = []

    async def run(self, query, session_id, run_id=None):
        self.calls.append(run_id)
        await asyncio.sleep(5)
        self.finished.append(run_id)
        return {"response": "done", "session_id": session_id}


@pytest.mark.asyncio
async def test_a_newer_run_interrupts_the_running_one():
    manager = BackgroundAgentManager(task_store="in_memory", lease_seconds=0.4)
    agent = SlowAgent()
    await manager.register_agent("agent", agent)
    await manager.register_task(
        task_id="task",
        agent_id="agent",
        query="work",
        schedule={"type": "manual"},
        overlap_policy=OverlapPolicy.CANCEL_PREVIOUS,
    )
    await manager.start()
    try:
        first = await manager.run_now("task")
        await wait_for(lambda: agent.calls)
        await manager.run_now("task")

        loop = asyncio.get_running_loop()
        start = loop.time()
        while (await manager.get_run(first.run_id)).status != RunStatus.CANCELLED:
            assert loop.time() - start < 3, "the first run was not interrupted"
            await asyncio.sleep(0.05)
        assert first.run_id not in agent.finished
    finally:
        await manager.shutdown()
