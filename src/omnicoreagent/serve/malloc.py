"""Cap the C allocator's arenas, so a server's threads do not each grow one.

The support desk's 30-minute server soak (2026-10-07) showed memory rising 2.6
to 2.8 MiB a minute with no plateau. The Python heap was not where it went:
once a session's state was released it grew about 27 allocated blocks a visit.
The rest was glibc. Each thread that allocates is given an arena, up to eight
for every core, and memory freed in an arena stays in it. OmniServe has many
such threads (the database calls, the telemetry writer, tool calls), so
resident memory climbed by about 95 KB a visit locally. The same run with two
arenas grew about 5 KB a visit and flattened (server, 2026-10-07).

``MALLOC_ARENA_MAX`` does this from outside, but only when the operator knows to
set it, so the server asks for it itself. An operator's own ``MALLOC_ARENA_MAX``
wins, and ``OMNICOREAGENT_SERVE_MALLOC_ARENAS=0`` leaves the allocator alone.
It only has an effect with glibc; anywhere else it does nothing.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

from omnicoreagent.core.logging import logger

DEFAULT_ARENAS = 2
_M_ARENA_MAX = -8  # glibc's <malloc.h>
_SETTING = "OMNICOREAGENT_SERVE_MALLOC_ARENAS"

# What the last call asked for and glibc accepted, so a deployment can say
# whether the cap took effect (the support desk's census reads it).
applied_arenas: int | None = None


def limit_malloc_arenas(*, libc: Any = None) -> int | None:
    """Ask glibc for at most N arenas; return N, or None when nothing was changed."""
    raw = os.environ.get(_SETTING, "").strip()
    try:
        arenas = int(raw) if raw else DEFAULT_ARENAS
    except ValueError:
        raise ValueError(f"{_SETTING} must be a whole number, got {raw!r}") from None
    if arenas <= 0 or os.environ.get("MALLOC_ARENA_MAX"):
        return None
    if libc is None:
        if not sys.platform.startswith("linux"):
            return None
        try:
            libc = ctypes.CDLL("libc.so.6")
        except OSError:
            return None
    mallopt = getattr(libc, "mallopt", None)
    if mallopt is None:
        return None
    if not mallopt(_M_ARENA_MAX, arenas):
        logger.debug("The allocator did not accept a cap of %s arenas", arenas)
        return None
    global applied_arenas
    applied_arenas = arenas
    return arenas
