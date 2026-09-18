"""Run deadlines that record why a run was stopped.

``asyncio.wait_for`` stops an overdue run by cancelling it, so the run itself
cannot tell a timeout from a caller cancelling it. ``run_with_timeout`` marks
the reason before cancelling; ``OmniCoreAgent.run`` reads it with
``current_stop_reason`` and records a ``timeout`` trace instead of
``cancelled``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass
class _StopReason:
    parent: _StopReason | None = None
    reason: str | None = None


_STOP_REASON: ContextVar[_StopReason | None] = ContextVar(
    "omnicoreagent_run_stop_reason",
    default=None,
)


def current_stop_reason() -> str | None:
    """Return why the enclosing deadline stopped this run, if it did."""
    box = _STOP_REASON.get()
    while box is not None:
        if box.reason is not None:
            return box.reason
        box = box.parent
    return None


async def run_with_timeout(awaitable: Awaitable[T], timeout: float | None) -> T:
    """Await ``awaitable`` with a deadline, recording a timeout as the stop reason.

    Raises ``asyncio.TimeoutError`` after the run has handled its
    cancellation. ``None`` or a non-positive timeout disables the deadline.
    """
    if timeout is None or timeout <= 0:
        return await awaitable
    box = _StopReason(parent=_STOP_REASON.get())
    token = _STOP_REASON.set(box)
    try:
        # The task copies the current context, so the run sees this box.
        task = asyncio.ensure_future(awaitable)
    finally:
        _STOP_REASON.reset(token)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    if task in done:
        return task.result()
    box.reason = "timeout"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    raise asyncio.TimeoutError(f"Run exceeded its {timeout} second deadline")
