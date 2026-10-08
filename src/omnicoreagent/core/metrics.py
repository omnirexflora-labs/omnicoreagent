"""Counters the runtime keeps for an operator, read by ``/prometheus``.

One process holds one registry. Each counter only ever grows, and takes labels
from small fixed sets (a status, a model name, a decision): never a run id, a
session id or a tool argument, which would grow a series for every request. A
counter that is given more label sets than ``MAX_SERIES`` counts the rest
under ``other``, so a mistake in a label cannot grow the scrape without bound
(the rc7 security review found a counter per path doing exactly that).
"""

from __future__ import annotations

import threading

# Label sets per counter. Real sets are a few dozen at most.
MAX_SERIES = 100

_OTHER = "other"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class CounterRegistry:
    """Named counters with labels, safe to bump from any thread."""

    def __init__(self) -> None:
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int]] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: int = 1, **labels: object) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            series = self._counters.setdefault(name, {})
            if key not in series and len(series) >= MAX_SERIES:
                key = tuple((k, _OTHER) for k, _ in key)
            series[key] = series.get(key, 0) + value

    def prometheus_lines(self) -> list[str]:
        lines: list[str] = []
        with self._lock:
            snapshot = {name: dict(series) for name, series in self._counters.items()}
        for name in sorted(snapshot):
            lines.append(f"# TYPE {name} counter")
            for key, value in sorted(snapshot[name].items()):
                if key:
                    rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in key)
                    lines.append(f"{name}{{{rendered}}} {value}")
                else:
                    lines.append(f"{name} {value}")
        return lines


COUNTERS = CounterRegistry()
