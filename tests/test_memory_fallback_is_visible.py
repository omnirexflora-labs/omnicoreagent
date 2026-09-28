"""Asking for a durable store without its URL says so where people see it.

Found writing Stores and scale (D7): MemoryRouter("sql") without
DATABASE_URL (and redis, mongodb without theirs) falls back to memory, and
the warning went to a logger with no handler, so nobody saw that runs would
not survive a restart. It is a RuntimeWarning now, which Python shows.
"""

from __future__ import annotations

import pytest

from omnicoreagent import MemoryRouter


@pytest.mark.parametrize(
    ("backend", "variable"),
    [("sql", "DATABASE_URL"), ("redis", "REDIS_URL"), ("mongodb", "MONGODB_URI")],
)
def test_a_missing_url_warns_visibly(backend, variable, monkeypatch):
    monkeypatch.delenv(variable, raising=False)
    with pytest.warns(RuntimeWarning, match=variable):
        router = MemoryRouter(backend)
    assert router.memory_store.__class__.__name__ == "InMemoryStore"
