"""Whether a tool call began executing.

A synchronous tool runs in a worker thread, and a thread cannot be killed. When
a call's time limit fires, the runtime has to know which of two things
happened: the call was still waiting for a thread (cancelling it stopped it, and
nothing happened), or the function was already running (the thread carries on
and its effect can land after the limit). The support desk chaos run
(2026-10-07) showed why the difference matters: a refund that had started and
then timed out was reported to the model as a plain timeout.

The runner opens a box for each call; the wrapper that the tool runs inside, in
the worker thread, sets it when the function actually starts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_BOX: ContextVar[dict | None] = ContextVar("omnicoreagent_call_start", default=None)


@contextmanager
def tracking_call_start(box: dict) -> Iterator[dict]:
    """Make ``box`` the one the calls made inside the block report into."""
    token = _BOX.set(box)
    try:
        yield box
    finally:
        _BOX.reset(token)


def current_start_box() -> dict | None:
    """The box of the call being made now; take it before handing work to a
    thread, which gets a copy of the context but should write to this one."""
    return _BOX.get()


def call_began(box: dict | None) -> bool:
    return bool(box and box.get("began"))
