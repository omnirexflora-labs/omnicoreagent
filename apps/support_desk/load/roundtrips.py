"""Count what the support desk asks of its database, and time its steps.

Used by ``tests/test_desk_roundtrips.py`` (a ceiling on the count) and by
``python apps/support_desk/load/stepbench.py`` (milliseconds per step). The
desk is built the way OmniServe builds it, in this process, against the fake
provider; nothing is stubbed, so every store call and every SQL transaction
the real runtime makes is seen.

A *store call* is one await on the memory store. A *transaction* is one
database transaction (SQLAlchemy ``begin``), which is at least one round trip
to a networked database. Both are counted under the outermost store method
that caused them, grouped as budgets, messages, run state, or other.
"""

from __future__ import annotations

import contextvars
import functools
import importlib.util
import inspect
import os
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent

CATEGORIES = ("budgets", "messages", "run_state", "other")
_MESSAGE_CALLS = {"store_message", "get_messages", "clear_memory", "mark_messages_summarized"}
_caller: contextvars.ContextVar[str | None] = contextvars.ContextVar("desk_caller", default=None)


def category_of(method: str) -> str:
    if "budget" in method:
        return "budgets"
    if "run_state" in method:
        return "run_state"
    if method in _MESSAGE_CALLS:
        return "messages"
    return "other"


class RoundTripCounter:
    """Counts store calls, transactions and statements, by caller."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.methods: Counter[str] = Counter()
        self.transactions: Counter[str] = Counter()
        self.statements: Counter[str] = Counter()

    def reset(self) -> None:
        for counter in (self.calls, self.methods, self.transactions, self.statements):
            counter.clear()

    def snapshot(self) -> dict:
        return {
            "calls": {c: self.calls[c] for c in CATEGORIES},
            "transactions": {c: self.transactions[c] for c in CATEGORIES},
            "statements": {c: self.statements[c] for c in CATEGORIES},
            "total_calls": sum(self.calls.values()),
            "total_transactions": sum(self.transactions.values()),
            "total_statements": sum(self.statements.values()),
            "methods": dict(self.methods),
        }

    def report(self) -> str:
        snap = self.snapshot()
        lines = [f"{'caller':<10} {'calls':>6} {'txns':>6} {'stmts':>6}"]
        for c in CATEGORIES:
            lines.append(
                f"{c:<10} {snap['calls'][c]:>6} {snap['transactions'][c]:>6} {snap['statements'][c]:>6}"
            )
        lines.append(
            f"{'total':<10} {snap['total_calls']:>6} {snap['total_transactions']:>6} {snap['total_statements']:>6}"
        )
        return "\n".join(lines)

    # -- wiring -----------------------------------------------------------

    def attach(self, store) -> None:
        """Wrap the store's async methods and listen to its engine."""
        from sqlalchemy import event

        counter = self
        for name, method in inspect.getmembers(store, inspect.iscoroutinefunction):
            if name.startswith("_"):
                continue

            def wrap(name=name, method=method):
                @functools.wraps(method)
                async def counted(*args, **kwargs):
                    token = None
                    if _caller.get() is None:
                        token = _caller.set(category_of(name))
                    counter.calls[category_of(name)] += 1
                    counter.methods[name] += 1
                    try:
                        return await method(*args, **kwargs)
                    finally:
                        if token is not None:
                            _caller.reset(token)

                return counted

            setattr(store, name, wrap())

        manager = store._sql_manager
        engines = [manager.get_engine()]
        read_engine = getattr(manager, "_read_engine", None)
        if read_engine is not None and read_engine is not engines[0]:
            engines.append(read_engine)
        for engine in engines:
            event.listen(engine, "begin", lambda conn: self._tick(self.transactions))
            event.listen(
                engine,
                "before_cursor_execute",
                lambda conn, cursor, statement, parameters, context, executemany: self._tick(
                    self.statements
                ),
            )

    def _tick(self, counter: Counter) -> None:
        counter[_caller.get() or "other"] += 1


def load_desk():
    spec = importlib.util.spec_from_file_location("support_desk_agent", DESK / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def desk_environment(provider_url: str, *, directory: str | None = None, database_url: str | None = None):
    """The environment the desk reads, pointed at the fake provider and a scratch directory."""
    root = Path(directory or tempfile.mkdtemp(prefix="desk-roundtrips-"))
    names = ("LLM_API_KEY", "DESK_BASE_URL", "DESK_DB", "DATABASE_URL", "DESK_FAKE_URL", "DESK_PROFILE")
    saved = {k: os.environ.get(k) for k in names}
    cwd = os.getcwd()
    os.environ.pop("LLM_API_KEY", None)
    os.environ.pop("DESK_BASE_URL", None)
    os.environ.pop("DESK_PROFILE", None)
    os.environ["DESK_DB"] = str(root / "desk.db")
    os.environ["DATABASE_URL"] = database_url or f"sqlite:///{root / 'memory.db'}"
    os.environ["DESK_FAKE_URL"] = f"{provider_url}/v1"
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def refund_scenario(agent, session_id: str, order: str = "1042") -> dict:
    """Order lookup, refund, approval, resume: three model calls, two tool calls."""
    paused = await agent.run(f"Please refund order {order}, $12.50.", session_id=session_id)
    assert paused["status"] == "awaiting_approval", paused
    (approval,) = paused["approvals"]
    await agent.resolve_approval(
        paused["run_id"], approval["approval_id"], decision="approve", approver="dana"
    )
    done = await agent.resume(paused["run_id"])
    assert done["status"] == "success", done
    return done


def sys_path_for_tests() -> None:
    if str(DESK) not in sys.path:
        sys.path.insert(0, str(DESK))
