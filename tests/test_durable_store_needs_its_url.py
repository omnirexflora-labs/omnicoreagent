"""Asking for a durable store without its URL is an error, not memory.

Found writing Stores and scale (D7): MemoryRouter("sql") without
DATABASE_URL (and redis, mongodb without theirs) fell back to memory with
a warning, so a deployment could run for weeks believing its runs would
survive a restart and resume in another process. The maintainer's decision
(2026-09-28): nobody asks for "sql" wanting memory; stop at startup and name
the variable.
"""

from __future__ import annotations

import pytest

from omnicoreagent import MemoryRouter


@pytest.mark.parametrize(
    ("backend", "variable"),
    [("sql", "DATABASE_URL"), ("redis", "REDIS_URL"), ("mongodb", "MONGODB_URI")],
)
def test_a_missing_url_is_an_error_naming_the_variable(backend, variable, monkeypatch):
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValueError, match=variable):
        MemoryRouter(backend)


def test_in_memory_needs_nothing():
    assert MemoryRouter("in_memory").memory_store.__class__.__name__ == "InMemoryStore"


def test_a_failed_switch_keeps_the_store_it_had(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    router = MemoryRouter("in_memory")
    store = router.memory_store

    with pytest.raises(ValueError, match="DATABASE_URL"):
        router.switch_memory_store("sql")

    assert router.memory_store_type == "in_memory" and router.memory_store is store
