"""The SQL store's budget methods survive a dropped connection, safely.

The pool no longer pings on checkout (the support desk ramp, 2026-10-07), so a
connection the server dropped fails the first statement that uses it. The
store's run-state and message methods retry once on a fresh connection. The
budget methods were written on another track and did not, so a restarted
database failed a run's charge. A read retries freely; a budget change retries
only if its transaction never reached COMMIT, because a guarded
``spent = spent + x`` that did commit would be applied twice.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from omnicoreagent.core.memory_store import sql_db_memory
from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

CHARGE = {"guard": [("cost", 2.0, 10.0)]}


@pytest.fixture
def store(tmp_path):
    store = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'budget.db'}")
    yield store
    sql_db_memory.close_all_sql_managers()


def _drop_next_connection(store):
    """The next checkout hands out a connection the server has dropped."""
    manager = store._sql_manager
    state = {"dropped": 0}

    def drop(dbapi_connection, record, proxy):
        if state["dropped"] == 0:
            state["dropped"] += 1
            dbapi_connection.close()

    for engine in (manager.get_engine(), manager._read_engine):
        event.listen(engine, "checkout", drop)
    return state


async def _spent(store) -> float:
    view = await store.get_budget_state("k")
    return (view or {"meters": {}})["meters"].get("cost", 0.0)


@pytest.mark.asyncio
async def test_a_budget_change_survives_a_dropped_connection(store):
    await store.apply_budget_change("k", CHARGE)  # also looks for a legacy counter
    state = _drop_next_connection(store)

    result = await store.apply_budget_change("k", CHARGE)

    assert state["dropped"] == 1
    assert result["refused"] is None
    assert await _spent(store) == 4.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, check",
    [
        ("get_budget_state", lambda v: v["meters"]["cost"] == 1.0),
        ("get_budget_grant_history", lambda v: v[0]["amount"] == 5.0),
        ("list_budget_holds", lambda v: v[0]["id"] == "h1"),
    ],
)
async def test_the_reads_survive_a_dropped_connection(store, method, check):
    await store.apply_budget_change(
        "k",
        {
            "guard": [("cost", 1.0, 10.0)],
            "hold": {"id": "h1", "meter": "cost", "amount": 1.0, "limit": 10.0, "run_id": "r"},
            "grant": {"meter": "cost", "amount": 5.0, "approver": "a", "note": "n"},
        },
    )
    await store.get_budget_state("k")  # the legacy look is done
    state = _drop_next_connection(store)

    assert check(await getattr(store, method)("k"))

    assert state["dropped"] == 1


@pytest.mark.asyncio
async def test_delete_survives_a_dropped_connection(store):
    await store.apply_budget_change("k", CHARGE)
    state = _drop_next_connection(store)

    await store.delete_budget_state("k")

    assert state["dropped"] == 1
    assert await store.get_budget_state("k") is None


@pytest.mark.asyncio
async def test_the_legacy_move_survives_a_dropped_connection(store):
    legacy = {"key": "k", "meters": {"cost": 3.0}, "grants": {}, "reservations": {}}
    await store.save_budget_state(legacy, None)
    state = _drop_next_connection(store)

    assert await _spent(store) == 3.0

    assert state["dropped"] == 1
    # Moved once: a second read does not add it again.
    assert await _spent(store) == 3.0


@pytest.mark.asyncio
async def test_a_budget_change_is_not_retried_after_its_commit_began(store, monkeypatch):
    """A drop during COMMIT leaves it unknown whether the charge landed. The
    commit here did land; a retry would charge it twice."""
    await store.apply_budget_change("k", CHARGE)
    real_commit = Session.commit
    calls = {"n": 0}

    def commit_then_drop(self):
        calls["n"] += 1
        real_commit(self)
        exc = OperationalError("COMMIT", {}, Exception("server closed the connection"))
        exc.connection_invalidated = True
        raise exc

    monkeypatch.setattr(Session, "commit", commit_then_drop)
    with pytest.raises(OperationalError):
        await store.apply_budget_change("k", CHARGE)
    monkeypatch.undo()

    assert calls["n"] == 1
    assert await _spent(store) == 4.0, "the charge was applied a second time"


@pytest.mark.asyncio
async def test_a_budget_change_dropped_before_commit_is_applied_once(store, monkeypatch):
    """A drop while the statements are sent wrote nothing, so the retry is the
    only application of the change."""
    await store.apply_budget_change("k", CHARGE)
    real_execute = Session.execute
    state = {"dropped": 0}

    def drop_once(self, statement, *args, **kwargs):
        if state["dropped"] == 0 and str(statement).lstrip().upper().startswith("UPDATE"):
            state["dropped"] += 1
            exc = OperationalError("UPDATE", {}, Exception("server closed the connection"))
            exc.connection_invalidated = True
            raise exc
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", drop_once)
    result = await store.apply_budget_change("k", CHARGE)
    monkeypatch.undo()

    assert state["dropped"] == 1
    assert result["refused"] is None
    assert await _spent(store) == 4.0
