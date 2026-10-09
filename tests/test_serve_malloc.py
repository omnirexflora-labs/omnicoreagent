"""OmniServe caps the C allocator's arenas, so many threads do not each grow one.

Server soak, 2026-10-07: the support desk's memory rose 2.6 to 2.8 MiB a minute
with no plateau. The Python heap was not the cause (about 27 allocated blocks a
visit once sessions were released). With glibc's default of eight arenas a core,
the threads that run the database calls and the telemetry writer each kept their
own arena and freed memory in it stayed there: resident memory grew about
95 KB a visit locally. The same run with ``MALLOC_ARENA_MAX=2`` grew about
5 KB a visit and flattened.
"""

from __future__ import annotations

import pytest

from omnicoreagent.serve import malloc
from omnicoreagent.serve.malloc import DEFAULT_ARENAS, limit_malloc_arenas

M_ARENA_MAX = -8


class FakeLibc:
    def __init__(self, result: int = 1):
        self.calls: list[tuple[int, int]] = []
        self.result = result

    def mallopt(self, option: int, value: int) -> int:
        self.calls.append((option, value))
        return self.result


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    monkeypatch.delenv("OMNICOREAGENT_SERVE_MALLOC_ARENAS", raising=False)


def test_the_arenas_are_capped_by_default():
    libc = FakeLibc()
    assert limit_malloc_arenas(libc=libc) == DEFAULT_ARENAS == 2
    assert libc.calls == [(M_ARENA_MAX, 2)]


def test_the_setting_changes_the_cap_and_zero_leaves_the_allocator_alone(monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_SERVE_MALLOC_ARENAS", "4")
    libc = FakeLibc()
    assert limit_malloc_arenas(libc=libc) == 4 and libc.calls == [(M_ARENA_MAX, 4)]

    monkeypatch.setenv("OMNICOREAGENT_SERVE_MALLOC_ARENAS", "0")
    libc = FakeLibc()
    assert limit_malloc_arenas(libc=libc) is None and libc.calls == []


def test_an_operators_own_malloc_arena_max_wins(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "8")
    libc = FakeLibc()
    assert limit_malloc_arenas(libc=libc) is None and libc.calls == []


def test_an_allocator_that_refuses_or_is_not_glibc_is_not_an_error():
    assert limit_malloc_arenas(libc=FakeLibc(result=0)) is None

    class NoMallopt:
        pass

    assert limit_malloc_arenas(libc=NoMallopt()) is None


def test_a_bad_setting_is_reported_not_ignored(monkeypatch):
    monkeypatch.setenv("OMNICOREAGENT_SERVE_MALLOC_ARENAS", "many")
    with pytest.raises(ValueError, match="OMNICOREAGENT_SERVE_MALLOC_ARENAS"):
        limit_malloc_arenas(libc=FakeLibc())


def test_omniserve_caps_the_arenas_when_it_is_built(monkeypatch):
    from omnicoreagent.serve import server

    called = []
    monkeypatch.setattr(server, "limit_malloc_arenas", lambda: called.append(1))

    class Agent:
        name = "a"

    try:
        server.OmniServe(Agent())
    except Exception:
        pass  # building the app needs more than this stub; the call came first
    assert called == [1]
    assert malloc  # the module the server imports it from
