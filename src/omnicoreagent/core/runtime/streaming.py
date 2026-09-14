"""Bounded public delivery shared by normal and nested agent runs."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class StreamDelivery:
    callback: Any
    run_id: str
    sequence: int = 0

    async def emit(self, event, *, agent_name, run_id, session_id, trace_id):
        self.sequence += 1
        await self.callback(
            {
                **event,
                "phase": "intermediate",
                "run_id": self.run_id,
                "actor_run_id": run_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "agent_name": agent_name,
                "sequence": self.sequence,
                "event_id": f"{self.run_id}:text:{self.sequence}",
            }
        )


current_delivery: ContextVar[StreamDelivery | None] = ContextVar(
    "omnicore_stream_delivery", default=None
)


async def stream_run(agent, query, *, session_id=None, run_id=None):
    """Yield live deltas then one complete result; closing cancels the shared run."""
    session_id = session_id or agent.generate_session_id()
    run_id = run_id or agent.generate_run_id()
    queue = asyncio.Queue(maxsize=256)

    async def execute():
        try:
            result = await agent.run(
                query, session_id=session_id, run_id=run_id, on_event=queue.put
            )
            terminal = {"type": "complete", **result}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            terminal = {
                "type": "error",
                "error": str(exc),
                "status": "error",
                "session_id": session_id,
                "run_id": run_id,
                "agent_name": agent.name,
            }
        await queue.put(terminal)

    task = asyncio.create_task(execute())
    pending = None
    try:
        while True:
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {task, pending}, return_when=asyncio.FIRST_COMPLETED
            )
            if pending not in done:
                # A provider or external owner may cancel the producer directly.
                # Do not wait forever for a terminal item it cannot enqueue.
                await task
                event = await pending
            else:
                event = pending.result()
            pending = None
            yield event
            if event["type"] in {"complete", "error"}:
                break
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
