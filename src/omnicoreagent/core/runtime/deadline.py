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

from omnicoreagent.core.logging import logger

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
        # Wait for the run to record its cancellation even if this caller is
        # cancelled again (anyio re-delivers cancellation at every await);
        # forwarding a second cancel would interrupt that cleanup.
        await complete_despite_cancellation(task)
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


_MISSING = object()

# Upper bound on finishing cleanup while the caller keeps being cancelled.
CLEANUP_BOUND_SECONDS = 10.0


async def complete_despite_cancellation(
    awaitable: Awaitable[T], bound_seconds: float = CLEANUP_BOUND_SECONDS
) -> T | None:
    """Run cleanup to completion even if the caller is cancelled again.

    Repeated cancellation of the caller is absorbed until the work finishes or
    ``bound_seconds`` pass; the caller then re-raises its own cancellation.
    Returns the work's result, or ``None`` if it failed or was cut off.

    The work runs in its own task, so its context variable changes (such as a
    trace ending and restoring its parent's telemetry context) are copied
    back to the caller, as if it had run inline.
    """
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    try:
        _propagate = task.get_context
    except AttributeError:  # pragma: no cover - not a Task
        _propagate = None
    deadline = loop.time() + bound_seconds
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            task.cancel()
            break
        try:
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue
    await asyncio.gather(task, return_exceptions=True)
    if _propagate is not None:
        for variable, value in _propagate().items():
            if variable.get(_MISSING) is not value:
                variable.set(value)
    if task.cancelled():
        logger.warning("Cleanup did not finish within %s seconds", bound_seconds)
        return None
    if task.exception() is not None:
        logger.warning(
            "Cleanup failed during cancellation: %s",
            task.exception().__class__.__name__,
        )
        return None
    return task.result()
