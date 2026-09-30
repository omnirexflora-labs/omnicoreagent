"""Ctrl-C before `omnicoreagent run` has started its run: noted, not raised.

Kept outside the ``cli`` package, whose import alone took 1-4 s on a loaded
host: a Ctrl-C in that window gave a traceback and exit 130, and 2 of 69
landed inside Python's import machinery, where the interrupt is swallowed,
and the run went ahead (the 0.5.0rc3 gate). The command's entry point
installs this before importing anything heavy; the run's own handler takes
over once the run exists.
"""

from __future__ import annotations

import os
import signal
import sys

_STATE: dict | None = None


def install() -> dict:
    """Note SIGINT and SIGTERM from now on; a second one exits 6 at once.

    Idempotent: the entry point and the command both call it.
    """
    global _STATE
    if _STATE is not None:
        return _STATE
    state = {"count": 0}

    def noted(signum, frame):
        state["count"] += 1
        if state["count"] > 1:
            sys.stderr.write("Stopped before the run started; nothing ran.\n")
            sys.stderr.flush()
            os._exit(6)
        sys.stderr.write(
            "Stopping once the agent has loaded; nothing has run (Ctrl-C again to stop now)...\n"
        )
        sys.stderr.flush()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, noted)
    _STATE = state
    return state
