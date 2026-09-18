"""Run deadlines that record why a run was stopped.

``asyncio.wait_for`` stops an overdue run by cancelling it, so the run itself
cannot tell a timeout from a caller cancelling it. ``run_with_timeout`` marks
the reason before cancelling; ``OmniCoreAgent.run`` reads it with
``current_stop_reason`` and records a ``timeout`` trace instead of
``cancelled``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def stop_after(timeout: float | None) -> AsyncIterator[None]:
    """Like ``asyncio.timeout``, in the current task, recording the stop reason.

    Code inside the block sees ``current_stop_reason() == "timeout"`` while it
    handles the cancellation, and the block raises ``asyncio.TimeoutError``.
    Running inline (no extra task) keeps work that already finished inside
    the block from being cancelled with its caller.
    """
    if timeout is None or timeout <= 0:
        yield
        return
    task = asyncio.current_task()
    box = _StopReason(parent=_STOP_REASON.get())
    token = _STOP_REASON.set(box)

    def expire() -> None:
        box.reason = "timeout"
        task.cancel()

    handle = asyncio.get_running_loop().call_later(timeout, expire)
    try:
        yield
    except asyncio.CancelledError:
        if box.reason == "timeout" and task.uncancel() == 0:
            raise asyncio.TimeoutError(
                f"Operation exceeded its {timeout} second deadline"
            ) from None
        raise
    finally:
        handle.cancel()
        _STOP_REASON.reset(token)
