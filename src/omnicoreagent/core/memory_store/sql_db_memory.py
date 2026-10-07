import json
import os
import random
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable
import uuid
import threading
import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from sqlalchemy import (
    Double,
    Integer,
    String,
    select,
    update,
    delete,
    Text,
    DateTime,
    create_engine,
    event,
    func,
    inspect,
    text,
    JSON,
    cast,
    type_coerce,
)
from sqlalchemy.engine import make_url
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.mutable import MutableDict
from omnicoreagent.core.budgets import (
    _GRANT_HISTORY_KEPT as GRANT_HISTORY_KEPT,
    budget_checks,
    counters_view,
    legacy_budget_parts,
    refusal_for,
)
from omnicoreagent.core.logging import logger
from omnicoreagent.core.sql_schema import create_tables
from omnicoreagent.core.memory_store.utils import utc_now_str
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,
)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig


def _agent_name_expression(session):
    # DynamicJSON is stored as TEXT. SQLite JSON_EXTRACT reads that text directly;
    # PostgreSQL requires a JSON cast before applying JSON operators.
    column = StorageMessage.msg_metadata
    json_column = (
        type_coerce(column, JSON)
        if session.bind.dialect.name == "sqlite"
        else cast(column, JSON)
    )
    return json_column["agent_name"].as_string()


# Concurrent operations are bounded by the threads that run them: one per
# connection the pool can lend (``SQLConnectionManager.executor``), so a
# connection never idles for want of a thread. A pool far beyond what the
# process can use only holds database connections nobody needs, and each is a
# server-side backend. The steady size is small, and the overflow brings the
# total up to 32, the bound the default executor used to impose. Both can be set
# per store (`DatabaseMessageStore(db_url, pool_size=..., max_overflow=...)`) or
# for the process (OMNICOREAGENT_SQL_POOL_SIZE, OMNICOREAGENT_SQL_MAX_OVERFLOW).
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 22


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


DEFAULT_MAX_KEY_LENGTH = 128
DEFAULT_MAX_VARCHAR_LENGTH = 256


# Arguments only a queue pool takes; SQLite's in-memory pool rejects them.
_POOL_ONLY_ARGUMENTS = frozenset(
    {"pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping", "pool_use_lifo"}
)
# How long a SQLite connection waits for another's write lock. SQLite's own
# default wait is zero, and a 50-run crowd raised "database is locked" on it
# (the P6 throughput run, 2026-10-07).
SQLITE_BUSY_TIMEOUT_MS = 30_000


def _sqlite_is_in_memory(db_url: str) -> bool:
    url = make_url(db_url)
    return url.database in (None, "", ":memory:") or "mode=memory" in str(url.query.get("uri", "")) or (
        "mode=memory" in (url.database or "")
    )


def _sqlite_connected(dbapi_connection, _record) -> None:
    """Let SQLite take concurrent runs: readers no longer block the writer and
    a writer waits for the lock instead of failing at once."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


class SQLConnectionManager:
    """
    SQL connection manager for efficient session management and connection pooling.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._engine = None
        self._session_factory = None
        self._read_session_factory = None
        self._read_engine = None
        self._session_count = 0
        # Stores using this pool; the last to close disposes it.
        self.users = 0
        # The threads the database calls run on, one per connection the pool
        # can lend (see ``executor``).
        self._executor: ThreadPoolExecutor | None = None
        self._pool_slots = DEFAULT_POOL_SIZE + DEFAULT_MAX_OVERFLOW
        logger.debug("SQLConnectionManager initialized")

    def executor(self) -> ThreadPoolExecutor:
        """The threads database calls run on, sized to the connection pool.

        Every call is a blocking driver call, so it hops to a thread. On the
        event loop's default executor those threads were shared with every
        other ``asyncio.to_thread`` in the process and capped at
        ``min(32, CPUs + 4)``: 6 on a 2-core container, however many
        connections the pool allowed, so database waits queued behind each
        other and behind a tool's file read (the support desk ramp,
        2026-10-07: 11% of samples were executor workers waiting, the event
        loop busy 18% of the time). One thread per connection the pool can
        lend (``pool_size + max_overflow``) never leaves a connection idle for
        want of a thread, and never holds more threads than connections.
        """
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=max(self._pool_slots, 1), thread_name_prefix="omnicore-sql"
                )
            return self._executor

    def initialize(self, db_url: str, **kwargs):
        """Initialize the SQL engine and session factory."""
        with self._lock:
            if self._engine is None:
                try:
                    connection_kwargs = {
                        "pool_size": _env_int(
                            "OMNICOREAGENT_SQL_POOL_SIZE", DEFAULT_POOL_SIZE
                        ),
                        "max_overflow": _env_int(
                            "OMNICOREAGENT_SQL_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW
                        ),
                        "pool_timeout": 30,
                        # Idle connections are replaced before a server or
                        # proxy idle timeout can drop them.
                        "pool_recycle": 1800,
                        # No ping on checkout: it sent a SELECT 1 before every
                        # operation, 16% of a 30-user profile (the support desk
                        # ramp, 2026-10-07). A connection the server dropped is
                        # survived by retrying the operation once (_with_retry),
                        # which costs nothing while connections are healthy.
                        "pool_pre_ping": False,
                        # Reuse the warmest connection, so a quiet pool shrinks.
                        "pool_use_lifo": True,
                        **kwargs,
                    }

                    self._pool_slots = int(connection_kwargs["pool_size"]) + int(
                        connection_kwargs["max_overflow"]
                    )
                    backend = make_url(db_url).get_backend_name()
                    if backend == "sqlite" and _sqlite_is_in_memory(db_url):
                        # One connection, lent to one thread at a time.
                        self._pool_slots = 1
                        self._initialize_sqlite_memory(db_url, connection_kwargs)
                        return
                    self._engine = create_engine(db_url, **connection_kwargs)
                    if backend == "sqlite":
                        event.listen(self._engine, "connect", _sqlite_connected)
                    self._session_factory = sessionmaker(bind=self._engine)
                    # Reads are single statements. Run in a transaction, each
                    # cost a BEGIN before it and a ROLLBACK after it, two round
                    # trips of three. A pool of autocommit connections of their
                    # own sends the statement alone. (Switching one pool's
                    # connections to autocommit and back per checkout costs a
                    # statement itself.)
                    self._read_engine = create_engine(
                        db_url,
                        **{**connection_kwargs, "isolation_level": "AUTOCOMMIT"},
                    )
                    if backend == "sqlite":
                        event.listen(self._read_engine, "connect", _sqlite_connected)
                    self._read_session_factory = sessionmaker(bind=self._read_engine)

                    logger.debug(f"[SQLManager] Created SQL connection pool: {db_url}")

                except Exception as e:
                    logger.error(f"[SQLManager] Failed to create SQL engine: {e}")
                    raise

    def _initialize_sqlite_memory(self, db_url: str, connection_kwargs: dict) -> None:
        """An in-memory SQLite database lives in one connection, so there is
        one engine for reads and writes and one connection for both.

        The pool arguments above (size, overflow, timeout, LIFO) are invalid
        for the pool SQLite's in-memory URL gets by default (a per-thread
        pool, where each thread would see its own empty database), and
        ``DatabaseMessageStore("sqlite://")`` failed on them (the P6
        throughput run, 2026-10-07). A queue pool of exactly one connection
        keeps the database alive and lends it to one session at a time, so
        threads take turns instead of interleaving transactions on it (a
        StaticPool would hand the one connection to all of them at once).
        """
        extra = {
            key: value
            for key, value in connection_kwargs.items()
            if key not in _POOL_ONLY_ARGUMENTS
        }
        engine = create_engine(
            db_url,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=connection_kwargs.get("pool_timeout", 30),
            connect_args={"check_same_thread": False},
            **extra,
        )
        self._engine = self._read_engine = engine
        self._session_factory = self._read_session_factory = sessionmaker(bind=engine)
        logger.debug(f"[SQLManager] Created in-memory SQLite engine: {db_url}")

    def get_session(self, read_only: bool = False):
        """Get a database session from the pool.

        A ``read_only`` session runs each statement in autocommit and must
        not be written through.
        """
        with self._lock:
            if self._session_factory is None:
                raise RuntimeError(
                    "SQLConnectionManager not initialized. Call initialize() first."
                )

            self._session_count += 1
            logger.debug(f"[SQLManager] SQL session usage count: {self._session_count}")
            if read_only:
                return self._read_session_factory()
            return self._session_factory()

    def release_session(self):
        """Release a session (decrement usage count)."""
        with self._lock:
            if self._session_count > 0:
                self._session_count -= 1
                logger.debug(
                    f"📉 [SQLManager] SQL session usage count: {self._session_count}"
                )

    def get_fresh_session(self):
        """Get a fresh session for background/external operations."""
        with self._lock:
            if self._session_factory is None:
                raise RuntimeError(
                    "SQLConnectionManager not initialized. Call initialize() first."
                )

            fresh_session = self._session_factory()
            logger.debug("[SQLManager] Created fresh session for background processing")
            return fresh_session

    def get_engine(self):
        """Get the SQLAlchemy engine."""
        return self._engine

    def dispose_pools(self) -> None:
        """Replace the pools, closing the idle connections in them."""
        with self._lock:
            for engine in (self._engine, self._read_engine):
                if engine is not None:
                    engine.dispose()

    def close_all(self):
        """Close all connections."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
                self._executor = None
            if self._engine:
                self._engine.dispose()
                if self._read_engine is not None:
                    self._read_engine.dispose()
                self._engine = None
                self._read_engine = None
                self._session_factory = None
                self._read_session_factory = None
                self._session_count = 0
                logger.debug("[SQLManager] Closed all SQL connections")


# One manager (engine and pool) per database URL: stores that point at the same
# database share a pool; stores that point at different databases never do.
_sql_managers: dict[str, SQLConnectionManager] = {}
_sql_managers_lock = threading.RLock()


def get_sql_manager(db_url: str) -> SQLConnectionManager:
    """The connection manager for one database URL."""
    with _sql_managers_lock:
        manager = _sql_managers.get(db_url)
        if manager is None:
            manager = _sql_managers[db_url] = SQLConnectionManager()
        return manager


def release_sql_manager(db_url: str) -> None:
    """One store is done with a database's pool; the last one closes it.

    Stores that name the same database share its pool, so closing one must
    not close it under another.
    """
    with _sql_managers_lock:
        manager = _sql_managers.get(db_url)
        if manager is None:
            return
        manager.users = max(0, manager.users - 1)
        if manager.users == 0:
            manager.close_all()
            del _sql_managers[db_url]


def close_all_sql_managers() -> None:
    """Close every SQL connection pool (for shutdown and tests)."""
    with _sql_managers_lock:
        for manager in _sql_managers.values():
            manager.close_all()
        _sql_managers.clear()


class DynamicJSON(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return value


class Base(DeclarativeBase):
    pass


class StorageMessage(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(
        String(DEFAULT_MAX_KEY_LENGTH),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH))
    role: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    timestamp: Mapped[str] = mapped_column(
        String(50), default=lambda: datetime.now(timezone.utc).isoformat()
    )
    msg_metadata: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(DynamicJSON), default={}
    )

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    inactive_reason: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    summary_id: Mapped[str | None] = mapped_column(
        String(DEFAULT_MAX_KEY_LENGTH), nullable=True, default=None
    )


class StorageRunState(Base):
    """One durable run record; ``data`` holds the record as JSON."""

    __tablename__ = "run_states"
    run_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        String(DEFAULT_MAX_KEY_LENGTH), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data: Mapped[str] = mapped_column(Text, nullable=False)


class StorageBudgetMeter(Base):
    """What one meter of one budget key has spent, holds and was granted."""

    __tablename__ = "budget_meters"
    key: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    meter: Mapped[str] = mapped_column(String(64), primary_key=True)
    spent: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    reserved: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    granted: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)


class StorageBudgetHold(Base):
    """One hold: budget set aside for a call that has not settled yet."""

    __tablename__ = "budget_holds"
    hold_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    key: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), index=True)
    meter: Mapped[str] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Double, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        String(DEFAULT_MAX_KEY_LENGTH), nullable=True, index=True
    )
    held_at: Mapped[str | None] = mapped_column(String(50), nullable=True)


class StorageBudgetGrant(Base):
    """One top-up a person granted to a budget key."""

    __tablename__ = "budget_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), index=True)
    meter: Mapped[str] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Double, nullable=False)
    approver: Mapped[str | None] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[str | None] = mapped_column(String(50), nullable=True)


class StorageBudgetState(Base):
    """A budget counter in the 0.5.x shape: ``data`` holds its meters and
    reservations. Nothing writes these now; one found is moved into
    ``budget_meters`` and ``budget_holds`` the first time its key is touched."""

    __tablename__ = "budget_states"
    key: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)


# SQLite has one writer at a time and, asked for the lock by a second
# transaction that already read, gives up at once instead of waiting. Within a
# process the budget writes take turns here, so threads queue instead of
# failing; other databases lock rows, and need nothing.
_sqlite_budget_lock = threading.Lock()
# A budget transaction the database aborted (a deadlock, a busy file) applied
# nothing, so it is run again, a few times.
_BUDGET_TRANSACTION_TRIES = 6


def _budget_write_lock(session):
    return _sqlite_budget_lock if session.bind.dialect.name == "sqlite" else nullcontext()


def _fallback_refusal(change: dict) -> dict:
    meter, amount, limit = budget_checks(change)[0]
    return {
        "meter": meter,
        "limit": float(limit or 0.0),
        "used": 0.0,
        "reserved": 0.0,
        "requested": amount,
    }


class DatabaseMessageStore(AbstractMemoryStore):
    """
    Database-backed message store for storing, retrieving, and clearing messages by session.
    """

    async def close(self) -> None:
        """Release this store's share of the database's connection pool; the
        last store using it closes it. Closing twice does nothing more."""
        if self._closed or not self.db_url:
            return
        self._closed = True
        release_sql_manager(self.db_url)

    def __init__(self, db_url: str = None, **kwargs: Any):
        self.db_url = db_url
        self.memory_config: dict[str, Any] = {}
        self.summary_config: dict[str, Any] = {}
        self.summarize_fn: Callable = None

        self._closed = False
        # Counters left in the 0.5.x shape are looked for once per key; see
        # ``_move_legacy_budget``.
        self._legacy_budgets_moved: set[str] = set()
        self._legacy_budgets_gone = False
        self._legacy_budgets_looked_for_any = False
        if db_url:
            self._sql_manager = get_sql_manager(db_url)
            self._sql_manager.initialize(db_url, **kwargs)
            with _sql_managers_lock:
                self._sql_manager.users += 1

            db_engine = self._sql_manager.get_engine()

            inspector = inspect(db_engine)
            existing_tables = inspector.get_table_names()

            if "messages" in existing_tables:
                self._migrate_add_columns(db_engine, inspector)
            # Creates only the tables that are missing (such as run_states),
            # tolerating another process creating them at the same moment
            # (the rc7 gate, C7-1: two replicas on an empty database).
            create_tables(db_engine, Base.metadata)

            logger.debug(f"DatabaseMessageStore initialized with: {db_url}")
        else:
            self._sql_manager = None
            logger.debug(
                "DatabaseMessageStore initialized without database (no db_url provided)"
            )

    def initialize_connection(self, db_url: str, **kwargs: Any):
        """Initialize the database connection if not already done."""
        if not hasattr(self, "_initialized") or not self._sql_manager._engine:
            self._sql_manager.initialize(db_url, **kwargs)

            db_engine = self._sql_manager.get_engine()

            inspector = inspect(db_engine)
            existing_tables = inspector.get_table_names()

            if "messages" not in existing_tables:
                create_tables(db_engine, Base.metadata)

            logger.debug("DatabaseMessageStore connection initialized")

    def _migrate_add_columns(self, db_engine, inspector):
        """
        Auto-migrate: add new columns if they don't exist.
        This ensures existing databases get the new lifecycle columns.
        """
        existing_columns = [col["name"] for col in inspector.get_columns("messages")]

        migrations = [
            ("status", "VARCHAR(20) DEFAULT 'active' NOT NULL"),
            ("inactive_reason", "VARCHAR(20) DEFAULT NULL"),
            ("summary_id", f"VARCHAR({DEFAULT_MAX_KEY_LENGTH}) DEFAULT NULL"),
        ]

        with db_engine.connect() as conn:
            for col_name, col_def in migrations:
                if col_name not in existing_columns:
                    try:
                        conn.execute(
                            text(
                                f"ALTER TABLE messages ADD COLUMN {col_name} {col_def}"
                            )
                        )
                        conn.commit()
                        logger.debug(f"Added column '{col_name}' to messages table")
                    except Exception as e:
                        logger.debug(f"Column '{col_name}' may already exist: {e}")

    async def _in_pool(self, function: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking database call on this database's own threads.

        Like ``asyncio.to_thread`` it carries the caller's context variables
        across, so what the runtime sets for a run is seen on the thread.
        """
        if self._sql_manager is None:
            return await asyncio.to_thread(function, *args)
        context = contextvars.copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            self._sql_manager.executor(), functools.partial(context.run, function, *args)
        )

    def _get_session(self, fresh_for_background: bool = False, read_only: bool = False):
        """Get a database session from the connection manager."""
        if self._sql_manager is None:
            raise RuntimeError("Database not configured - no db_url provided")
        if fresh_for_background:
            return self._sql_manager.get_fresh_session()
        else:
            return self._sql_manager.get_session(read_only=read_only)

    _retry_state = threading.local()

    def _commit(self, session) -> None:
        """Commit, marking that a failure from here on cannot be retried.

        A connection dropped during COMMIT leaves it unknown whether the write
        landed, and writing it again could duplicate it. The statements are
        sent first (flush), outside that window: a drop while sending them
        wrote nothing and is safe to retry.
        """
        session.flush()
        self._retry_state.committing = True
        session.commit()
        self._retry_state.committing = False

    def _with_retry(self, operation: Callable[[], Any]) -> Any:
        """Run one database operation, once more if its connection was dropped.

        With no ping on checkout, a pooled connection the server has since
        closed (a restart, an idle timeout, a failover) fails the first
        statement that uses it. SQLAlchemy then discards it and the other
        pooled connections older than the failure, so the second attempt gets a
        fresh one. Only a dropped connection is retried, and not once a COMMIT
        has begun (``_commit``). The operation opens its own session, so it
        must be safe to run again from the start.
        """
        for attempt in (1, 2):
            self._retry_state.committing = False
            try:
                return operation()
            except Exception as exc:
                if attempt == 2 or self._retry_state.committing:
                    raise
                if isinstance(exc, DBAPIError):
                    # SQLAlchemy has already discarded the connection, and the
                    # pooled ones older than it.
                    dropped = exc.connection_invalidated
                else:
                    # The driver refused before SQLAlchemy could wrap it (a
                    # connection it already knew was closed, when the read
                    # session set its isolation level). Nothing invalidated
                    # the rest of the pool, so replace it.
                    engine = self._sql_manager.get_engine()
                    dropped = engine is not None and engine.dialect.is_disconnect(
                        exc, None, None
                    )
                    if dropped:
                        self._sql_manager.dispose_pools()
                if not dropped:
                    raise
                logger.warning(f"[SQLManager] Connection dropped, retrying once: {exc}")

    def _rollback_quietly(self, session) -> None:
        """Roll back after a failure; a dropped connection has nothing to undo."""
        if session:
            try:
                session.rollback()
            except Exception as e:  # noqa: BLE001 - the original error is the one to report.
                logger.debug(f"Rollback after a failed operation also failed: {e}")

    def _release_session(self, session):
        """Release a session back to the pool."""
        if session:
            try:
                session.close()
                if not hasattr(session, "_is_fresh_session"):
                    self._sql_manager.release_session()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")

    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )
        self.memory_config = {"mode": mode, "value": value}
        if summary_config:
            self.summary_config = SummaryConfig(**summary_config)
        if summarize_fn:
            self.summarize_fn = summarize_fn

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str = None,
    ) -> None:
        """Store a message in the database."""
        if metadata is None:
            metadata = {}

        def _store_once():
            session = None
            try:
                session = self._get_session()
                msg = StorageMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                    msg_metadata=metadata,
                    status="active",
                    timestamp=utc_now_str(),
                )
                session.add(msg)
                self._commit(session)
            except Exception:
                self._rollback_quietly(session)
                raise
            finally:
                self._release_session(session)

        # A failed write is raised, not logged: a message that was never stored
        # would look stored, and the run would carry on without it (found
        # merging the P6 tracks, 2026-10-07). The run fails with the store's
        # error, and its record still holds the message.
        try:
            await self._in_pool(self._with_retry, _store_once)
        except Exception as e:
            logger.error(f"Failed to store message: {e}")
            raise

    async def get_messages(
        self, session_id: str = None, agent_name: str | None = None
    ) -> list[dict[str, Any]]:
        def _fetch_once():
            session = None
            try:
                session = self._get_session(fresh_for_background=False, read_only=True)
                query = session.query(StorageMessage).filter(
                    StorageMessage.status == "active"
                )

                if session_id:
                    query = query.filter(StorageMessage.session_id == session_id)

                if agent_name:
                    query = query.filter(_agent_name_expression(session) == agent_name)

                messages = query.order_by(StorageMessage.timestamp.asc()).all()

                return [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "session_id": m.session_id,
                        "timestamp": m.timestamp.timestamp()
                        if isinstance(m.timestamp, datetime)
                        else m.timestamp,
                        "msg_metadata": m.msg_metadata,
                    }
                    for m in messages
                ]
            finally:
                self._release_session(session)

        def _fetch_messages():
            try:
                return self._with_retry(_fetch_once)
            except Exception as e:
                logger.error(f"Failed to get messages: {e}")
                return []

        result = await self._in_pool(_fetch_messages)

        result, summary_msg, summarized_ids = await apply_summarization_logic(
            messages=result,
            memory_config=self.memory_config,
            summary_config=self.summary_config,
            summarize_fn=self.summarize_fn,
            agent_name=agent_name,
        )

        if summarized_ids and summary_msg:
            summary_id = str(uuid.uuid4())
            summary_msg["id"] = summary_id

            def _background_persist_summary():
                session = None
                try:
                    session = self._get_session(fresh_for_background=True)

                    summary_storage_msg = StorageMessage(
                        id=summary_id,
                        session_id=session_id,
                        role=summary_msg["role"],
                        content=summary_msg["content"],
                        msg_metadata=summary_msg["msg_metadata"],
                        status="active",
                        timestamp=utc_now_str(),
                    )
                    session.add(summary_storage_msg)

                    retention = getattr(
                        self.summary_config.retention_policy,
                        "value",
                        self.summary_config.retention_policy,
                    )

                    if retention == "delete":
                        session.query(StorageMessage).filter(
                            StorageMessage.id.in_(summarized_ids)
                        ).delete(synchronize_session=False)
                        logger.debug(
                            f"Deleted {len(summarized_ids)} summarized messages"
                        )
                    else:
                        session.query(StorageMessage).filter(
                            StorageMessage.id.in_(summarized_ids)
                        ).update(
                            {
                                "status": "inactive",
                                "inactive_reason": "summarized",
                                "summary_id": summary_id,
                            },
                            synchronize_session=False,
                        )
                        logger.debug(f"Marked {len(summarized_ids)} messages inactive")

                    session.commit()
                except Exception as e:
                    logger.error(f"Background persistence failed: {e}")
                    if session:
                        session.rollback()
                finally:
                    self._release_session(session)

            threading.Thread(target=_background_persist_summary, daemon=True).start()

        return result

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        # Run off the event loop like every other operation: this one used to
        # block it for the whole delete.
        def _clear_once():
            session = None
            try:
                session = self._get_session(fresh_for_background=False)

                if session_id and agent_name:
                    query = session.query(StorageMessage).filter(
                        StorageMessage.session_id == session_id,
                        _agent_name_expression(session) == agent_name,
                    )
                    query.delete()
                elif session_id:
                    query = session.query(StorageMessage).filter(
                        StorageMessage.session_id == session_id
                    )
                    query.delete()
                elif agent_name:
                    query = session.query(StorageMessage).filter(
                        _agent_name_expression(session) == agent_name
                    )
                    query.delete()
                else:
                    session.query(StorageMessage).delete()

                self._commit(session)
                logger.debug(
                    f"Cleared memory for session_id={session_id}, agent_name={agent_name}"
                )
            except Exception:
                self._rollback_quietly(session)
                raise
            finally:
                self._release_session(session)

        def _clear():
            try:
                self._with_retry(_clear_once)
            except Exception as e:
                logger.error(f"Failed to clear memory: {e}")

        await self._in_pool(_clear)

    async def mark_messages_summarized(
        self,
        message_ids: list[str],
        summary_id: str,
        retention_policy: str = "keep",
    ) -> None:
        """
        Mark messages as summarized (inactive or delete based on policy). done in background

        Args:
            message_ids: List of message IDs to mark as summarized
            summary_id: ID of the summary message that replaces these
            retention_policy: 'keep' to mark inactive, 'delete' to remove
        """
        if not message_ids:
            return

        if not message_ids:
            return

        def _mark_once():
            session = None
            try:
                session = self._get_session(fresh_for_background=False)

                if retention_policy == "delete":
                    session.query(StorageMessage).filter(
                        StorageMessage.id.in_(message_ids)
                    ).delete(synchronize_session=False)
                    logger.debug(f"Deleted {len(message_ids)} summarized messages")
                else:
                    session.query(StorageMessage).filter(
                        StorageMessage.id.in_(message_ids)
                    ).update(
                        {
                            "status": "inactive",
                            "inactive_reason": "summarized",
                            "summary_id": summary_id,
                        },
                        synchronize_session=False,
                    )
                    logger.debug(
                        f"Marked {len(message_ids)} messages as summarized (inactive)"
                    )

                self._commit(session)

            except Exception:
                self._rollback_quietly(session)
                raise
            finally:
                self._release_session(session)

        def _mark():
            try:
                self._with_retry(_mark_once)
            except Exception as e:
                logger.error(f"Failed to mark messages as summarized: {e}")

        await self._in_pool(_mark)

    # --- run state ---------------------------------------------------------

    async def save_run_state(self, record: dict, expected_version: int | None) -> int:
        from sqlalchemy.exc import IntegrityError

        from omnicoreagent.core.runs import RunStateConflict

        run_id = record["run_id"]
        version = (expected_version or 0) + 1
        data = json.dumps({**record, "version": version}, default=str)

        def _save_once() -> int:
            session = self._get_session()
            try:
                if expected_version is None:
                    session.add(
                        StorageRunState(
                            run_id=run_id,
                            session_id=record.get("session_id"),
                            status=record.get("status", "running"),
                            version=version,
                            created_at=record.get("created_at"),
                            data=data,
                        )
                    )
                    try:
                        self._commit(session)
                    except IntegrityError:
                        session.rollback()
                        raise RunStateConflict(f"Run {run_id} already exists") from None
                    return version
                # Compare and swap on the version.
                result = session.execute(
                    update(StorageRunState)
                    .where(
                        StorageRunState.run_id == run_id,
                        StorageRunState.version == expected_version,
                    )
                    .values(
                        status=record.get("status", "running"),
                        version=version,
                        data=data,
                    )
                )
                self._commit(session)
                if result.rowcount != 1:
                    raise RunStateConflict(
                        f"Run {run_id} changed since version {expected_version}"
                    )
                return version
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _save_once)

    async def get_run_state(self, run_id: str) -> dict | None:
        def _get():
            session = self._get_session(read_only=True)
            try:
                row = session.get(StorageRunState, run_id)
                return json.loads(row.data) if row is not None else None
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _get)

    async def list_run_states(
        self, session_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        def _list():
            session = self._get_session(read_only=True)
            try:
                query = session.query(StorageRunState)
                if session_id is not None:
                    query = query.filter(StorageRunState.session_id == session_id)
                if status is not None:
                    query = query.filter(StorageRunState.status == status)
                rows = query.order_by(StorageRunState.created_at).limit(limit).all()
                return [json.loads(row.data) for row in rows]
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _list)

    async def delete_finished_run_states(self, *, before: str, statuses: tuple[str, ...]) -> int:
        def _delete() -> int:
            session = self._get_session()
            try:
                result = session.execute(
                    delete(StorageRunState).where(
                        StorageRunState.status.in_(list(statuses)),
                        StorageRunState.created_at < before,
                    )
                )
                self._commit(session)
                return int(result.rowcount or 0)
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _delete)

    # --- budgets -----------------------------------------------------------
    # One row per (key, meter) holding spent, reserved and granted; one row per
    # hold; one row per grant. Every change to a counter is a single guarded
    # UPDATE (``spent = spent + :x``, limit checked in the WHERE), so nothing is
    # read, changed in Python and written back, and a crowd of runs on one
    # application key queues on a row lock for the length of one statement
    # instead of conflicting and retrying (the support desk ramp, 2026-10-07).
    # Meters are rows, not columns: a new meter needs no schema change, and a
    # run that touches the tool-call meter does not lock the cost meter.

    async def delete_budget_state(self, key: str) -> None:
        def _delete():
            session = self._get_session()
            try:
                for table in (
                    StorageBudgetMeter,
                    StorageBudgetHold,
                    StorageBudgetGrant,
                    StorageBudgetState,
                ):
                    session.execute(delete(table).where(table.key == key))
                self._commit(session)
            except Exception:
                self._rollback_quietly(session)
                raise
            finally:
                self._release_session(session)

        await self._in_pool(self._with_retry, _delete)

    async def get_budget_state(self, key: str) -> dict | None:
        await self._move_legacy_budget(key)

        def _get():
            session = self._get_session()
            try:
                rows = session.execute(
                    select(
                        StorageBudgetMeter.meter,
                        StorageBudgetMeter.spent,
                        StorageBudgetMeter.reserved,
                        StorageBudgetMeter.granted,
                    ).where(StorageBudgetMeter.key == key)
                ).all()
                return counters_view(
                    key,
                    {
                        meter: {"spent": spent, "reserved": reserved, "granted": granted}
                        for meter, spent, reserved, granted in rows
                    },
                )
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _get)

    async def get_budget_states(self, keys: list[str]) -> dict[str, dict | None]:
        """Several counters from one read (the run's end reads all its scopes)."""
        for key in keys:
            await self._move_legacy_budget(key)

        def _get():
            session = self._get_session()
            try:
                rows = session.execute(
                    select(
                        StorageBudgetMeter.key,
                        StorageBudgetMeter.meter,
                        StorageBudgetMeter.spent,
                        StorageBudgetMeter.reserved,
                        StorageBudgetMeter.granted,
                    ).where(StorageBudgetMeter.key.in_(list(keys)))
                ).all()
                counters: dict[str, dict[str, dict[str, float]]] = {key: {} for key in keys}
                for key, meter, spent, reserved, granted in rows:
                    counters[key][meter] = {
                        "spent": spent,
                        "reserved": reserved,
                        "granted": granted,
                    }
                return {key: counters_view(key, counters[key]) for key in keys}
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _get)

    async def get_budget_grant_history(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)

        def _get():
            session = self._get_session()
            try:
                rows = session.execute(
                    select(StorageBudgetGrant)
                    .where(StorageBudgetGrant.key == key)
                    .order_by(StorageBudgetGrant.id.desc())
                    .limit(GRANT_HISTORY_KEPT)
                ).scalars()
                return [
                    {
                        "meter": row.meter,
                        "amount": row.amount,
                        "approver": row.approver,
                        "note": row.note,
                        "granted_at": row.granted_at,
                    }
                    for row in reversed(list(rows))
                ]
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _get)

    async def list_budget_holds(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)

        def _list():
            session = self._get_session()
            try:
                rows = session.execute(
                    select(StorageBudgetHold).where(StorageBudgetHold.key == key)
                ).scalars()
                return [
                    {
                        "id": row.hold_id,
                        "meter": row.meter,
                        "amount": row.amount,
                        "run_id": row.run_id,
                        "held_at": row.held_at,
                    }
                    for row in rows
                ]
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _list)

    # A store that applies several keys' changes in one transaction says so,
    # and the budget ledger then hands it a whole call's changes at once.
    batches_budget_changes = True

    async def apply_budget_change(self, key: str, change: dict) -> dict:
        return (await self.apply_budget_changes([(key, change)]))[0]

    async def apply_budget_changes(self, changes: list[tuple[str, dict]]) -> list[dict]:
        """Every key's change of one call, in ONE transaction: all or none.

        A model call holds its cost on the request, session and application
        counters and settles all three afterwards. Applied one key at a time,
        each was a thread hop, a connection checkout and a commit: 27 of the
        60 transactions of a support desk refund run (the support desk
        ramp, 2026-10-07). The answers come back in the order of ``changes``.
        If any key refuses, nothing is applied on any key, and the refusal
        reported is the first in the caller's order (the request scope is
        listed first), whatever order the rows were locked in.
        """
        for key, _ in changes:
            await self._move_legacy_budget(key)
        return await self._in_pool(
            self._with_retry, lambda: self._apply_budget_changes(changes)
        )

    def _apply_budget_changes(self, changes: list[tuple[str, dict]]) -> list[dict]:
        """The changes, once, however many times the database aborts them.

        A dropped connection is retried by ``_with_retry`` around this method,
        and only while the transaction has not begun to commit (``_commit``
        marks that point). The guarded UPDATEs add to a counter, so a change
        that did commit and is run again would be charged twice; the same
        reason the retry never follows a COMMIT.
        """
        # Rows are locked in key order, so two calls that share two counters
        # never wait on each other in opposite orders and deadlock. The sort is
        # stable: a key listed twice keeps its changes in the caller's order.
        order = sorted(range(len(changes)), key=lambda i: changes[i][0])
        for attempt in range(_BUDGET_TRANSACTION_TRIES):
            # A commit that failed on a lock applied nothing; the attempt that
            # follows starts with a clean mark.
            self._retry_state.committing = False
            session = self._get_session()
            try:
                with _budget_write_lock(session):
                    try:
                        results: list[dict | None] = [None] * len(changes)
                        refused_at = None
                        for index in order:
                            key, change = changes[index]
                            results[index] = self._budget_transaction(session, key, change)
                            if results[index]["refused"] is not None:
                                refused_at = index
                                break
                        if refused_at is None:
                            self._commit(session)
                        else:
                            session.rollback()
                    except Exception:
                        self._rollback_quietly(session)
                        raise
                if refused_at is not None:
                    return self._describe_refusals(session, changes, refused_at)
                return results  # type: ignore[return-value]
            except OperationalError as exc:
                # A deadlock the database broke, or SQLite's file lock held by
                # another process past its own wait: nothing was applied, so
                # the same change is safe to run again.
                text_of_error = str(exc).lower()
                if attempt + 1 >= _BUDGET_TRANSACTION_TRIES or not (
                    "deadlock" in text_of_error
                    or "database is locked" in text_of_error
                    or "could not serialize" in text_of_error
                ):
                    raise
                time.sleep(0.01 * (attempt + 1) * (0.5 + random.random()))
            finally:
                self._release_session(session)
        keys = ", ".join(key for key, _ in changes)
        raise RuntimeError(f"Could not record the budget change for {keys}")  # pragma: no cover

    def _budget_transaction(self, session, key: str, change: dict) -> dict:
        """The change as one transaction. Holds are removed first (the hold row
        is what arbitrates two settlers), then each meter is updated once, in
        name order so two changes never wait on each other's rows."""
        deltas: dict[str, list[float]] = {}  # meter -> spent, reserved, granted
        limits: dict[str, list[float]] = {}  # meter -> [amount that must fit, limit]

        def delta(meter: str) -> list[float]:
            return deltas.setdefault(meter, [0.0, 0.0, 0.0])

        def must_fit(meter: str, amount: float, limit: float | None) -> None:
            if limit is None:
                return
            entry = limits.setdefault(meter, [0.0, float(limit)])
            entry[0] += amount
            entry[1] = min(entry[1], float(limit))

        released = 0

        # Each statement is a network round trip on a remote database, and a
        # transaction is several of them. Where the database can answer a
        # DELETE or an UPDATE with the row it changed (``RETURNING``:
        # PostgreSQL, SQLite 3.35+), the SELECT before and the SELECT after
        # are not sent: a refund run made 72 budget statements in 11
        # transactions (the support desk ramp, 2026-10-07).
        dialect = session.bind.dialect
        delete_returning = bool(getattr(dialect, "delete_returning", False))
        update_returning = bool(getattr(dialect, "update_returning", False))

        def remove_hold(hold_id: str) -> bool:
            if delete_returning:
                # The hold row is what arbitrates two settlers: only the one
                # whose DELETE removes it gets the row back.
                held = session.execute(
                    delete(StorageBudgetHold)
                    .where(StorageBudgetHold.hold_id == hold_id)
                    .returning(StorageBudgetHold.meter, StorageBudgetHold.amount)
                    .execution_options(synchronize_session=False)
                ).first()
                if held is None:
                    return False
                delta(held.meter)[1] -= float(held.amount)
                return True
            held = session.execute(
                select(StorageBudgetHold.meter, StorageBudgetHold.amount).where(
                    StorageBudgetHold.hold_id == hold_id
                )
            ).first()
            if held is None:
                return False
            gone = session.execute(
                delete(StorageBudgetHold).where(StorageBudgetHold.hold_id == hold_id)
            ).rowcount
            if gone != 1:  # another settler took it
                return False
            delta(held.meter)[1] -= float(held.amount)
            return True

        settle = change.get("settle")
        if settle:
            removed = remove_hold(settle["id"])
            if settle.get("spend") is not None and (removed or settle.get("even_if_released")):
                delta(settle["meter"])[0] += float(settle["spend"])
        wanted = list(change.get("release_runs") or [])
        if wanted and delete_returning:
            # The holds those runs left, removed and counted in one statement.
            for gone in session.execute(
                delete(StorageBudgetHold)
                .where(StorageBudgetHold.key == key, StorageBudgetHold.run_id.in_(wanted))
                .returning(StorageBudgetHold.meter, StorageBudgetHold.amount)
                .execution_options(synchronize_session=False)
            ).all():
                delta(gone.meter)[1] -= float(gone.amount)
                released += 1
        elif wanted:
            standing = session.execute(
                select(StorageBudgetHold.hold_id).where(
                    StorageBudgetHold.key == key, StorageBudgetHold.run_id.in_(wanted)
                )
            ).scalars().all()
            for hold_id in standing:
                if remove_hold(hold_id):
                    released += 1
        hold = change.get("hold")
        if hold and hold.get("amount"):
            delta(hold["meter"])[1] += float(hold["amount"])
            must_fit(hold["meter"], float(hold["amount"]), hold.get("limit"))
        for meter, amount, limit in change.get("guard") or []:
            delta(meter)[0] += float(amount)
            must_fit(meter, float(amount), limit)
        for meter, amount in change.get("add") or []:
            delta(meter)[0] += float(amount)
        grant = change.get("grant")
        if grant:
            delta(grant["meter"])[2] += float(grant["amount"])

        table = StorageBudgetMeter
        refused_meter = None
        totals: dict[str, float] = {}
        for meter in sorted(deltas):
            spent, reserved, granted = deltas[meter]
            if not (spent or reserved or granted):
                continue
            statement = (
                update(table)
                .where(table.key == key, table.meter == meter)
                .values(
                    spent=table.spent + spent,
                    reserved=table.reserved + reserved,
                    granted=table.granted + granted,
                )
            )
            if meter in limits:
                fit, limit = limits[meter]
                # The limit check and the change are this one statement.
                statement = statement.where(
                    table.spent + table.reserved + fit <= limit + table.granted
                )
            # A meter whose spend changes answers with its new total.
            returning = update_returning and bool(spent)
            if returning:
                statement = statement.returning(table.spent).execution_options(
                    synchronize_session=False
                )

            def run(statement=statement, returning=returning) -> bool:
                result = session.execute(statement)
                if not returning:
                    return result.rowcount != 0
                row = result.first()
                if row is not None:
                    totals[meter] = float(row[0])
                return row is not None

            if not run():
                # Either the row does not exist yet, or the change does not fit.
                self._ensure_meter_row(session, key, meter)
                if not run():
                    refused_meter = meter
                    break
        if refused_meter is not None:
            return {"refused": {"meter": refused_meter}, "totals": {}, "released": 0}
        if hold and hold.get("amount"):
            session.add(
                StorageBudgetHold(
                    hold_id=hold["id"],
                    key=key,
                    meter=hold["meter"],
                    amount=float(hold["amount"]),
                    run_id=hold.get("run_id"),
                    held_at=hold.get("held_at"),
                )
            )
        if grant:
            session.add(
                StorageBudgetGrant(
                    key=key,
                    meter=grant["meter"],
                    amount=float(grant["amount"]),
                    approver=grant.get("approver"),
                    note=grant.get("note"),
                    granted_at=grant.get("granted_at"),
                )
            )
        session.flush()
        # Only a database that cannot return the new total still has to read it.
        unread = [m for m, d in deltas.items() if d[0] and m not in totals]
        if unread:
            for meter, spent in session.execute(
                select(table.meter, table.spent).where(
                    table.key == key, table.meter.in_(unread)
                )
            ):
                totals[meter] = float(spent)
        return {"refused": None, "totals": totals, "released": released}

    def _ensure_meter_row(self, session, key: str, meter: str) -> None:
        values = {"key": key, "meter": meter, "spent": 0.0, "reserved": 0.0, "granted": 0.0}
        dialect = session.bind.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert

            session.execute(dialect_insert(StorageBudgetMeter).values(values).on_conflict_do_nothing())
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert

            session.execute(dialect_insert(StorageBudgetMeter).values(values).on_conflict_do_nothing())
        elif dialect in ("mysql", "mariadb"):
            from sqlalchemy.dialects.mysql import insert as dialect_insert

            session.execute(dialect_insert(StorageBudgetMeter).values(values).prefix_with("IGNORE"))
        else:
            try:
                with session.begin_nested():
                    session.execute(StorageBudgetMeter.__table__.insert().values(values))
            except IntegrityError:
                pass

    def _describe_refusals(
        self, session, changes: list[tuple[str, dict]], found_at: int
    ) -> list[dict]:
        """What the refused call ran into, for the error a person reads: the
        first check, in the caller's order, that does not fit what the
        counters hold now. ``found_at`` is the change the row locks met first;
        it is used when the counters have moved on and nothing refuses any more."""
        rows = session.execute(
            select(
                StorageBudgetMeter.key,
                StorageBudgetMeter.meter,
                StorageBudgetMeter.spent,
                StorageBudgetMeter.reserved,
                StorageBudgetMeter.granted,
            ).where(StorageBudgetMeter.key.in_(sorted({key for key, _ in changes})))
        ).all()
        counters: dict[str, dict[str, dict[str, float]]] = {}
        for key, meter, spent, reserved, granted in rows:
            counters.setdefault(key, {})[meter] = {
                "spent": spent,
                "reserved": reserved,
                "granted": granted,
            }
        refusal, at = None, found_at
        for index, (key, change) in enumerate(changes):
            refusal = refusal_for(budget_checks(change), counters.get(key, {}))
            if refusal is not None:
                at = index
                break
        else:
            refusal = _fallback_refusal(changes[found_at][1])
        return [
            {"refused": refusal if index == at else None, "totals": {}, "released": 0}
            for index in range(len(changes))
        ]

    # The two helpers below keep a counter written by 0.5.x readable. It was one
    # JSON document per key in ``budget_states``; the first time a key is
    # touched its document is moved into rows, in one transaction that deletes
    # the document first, so two workers moving it cannot both add it.

    async def _move_legacy_budget(self, key: str) -> None:
        if self._legacy_budgets_gone or key in self._legacy_budgets_moved:
            return
        await self._in_pool(self._with_retry, lambda: self._move_legacy_budget_sync(key))

    def _move_legacy_budget_sync(self, key: str) -> None:
        session = self._get_session()
        try:
            with _budget_write_lock(session):
                row = session.get(StorageBudgetState, key)
                if row is None:
                    if len(self._legacy_budgets_moved) > 10_000:
                        self._legacy_budgets_moved.clear()
                    self._legacy_budgets_moved.add(key)
                    if not self._legacy_budgets_looked_for_any:
                        self._legacy_budgets_looked_for_any = True
                        if session.execute(select(func.count()).select_from(StorageBudgetState)).scalar() == 0:
                            # Nothing in the old shape anywhere: stop looking.
                            self._legacy_budgets_gone = True
                    return
                try:
                    data = json.loads(row.data)
                    taken = session.execute(
                        delete(StorageBudgetState).where(StorageBudgetState.key == key)
                    ).rowcount
                    if taken == 1:
                        parts = legacy_budget_parts(data)
                        table = StorageBudgetMeter
                        for meter in sorted(parts["counters"]):
                            values = parts["counters"][meter]
                            statement = (
                                update(table)
                                .where(table.key == key, table.meter == meter)
                                .values(
                                    spent=table.spent + values["spent"],
                                    reserved=table.reserved + values["reserved"],
                                    granted=table.granted + values["granted"],
                                )
                            )
                            if session.execute(statement).rowcount == 0:
                                self._ensure_meter_row(session, key, meter)
                                session.execute(statement)
                        for hold_id, held in parts["holds"].items():
                            session.add(
                                StorageBudgetHold(
                                    hold_id=hold_id,
                                    key=key,
                                    meter=held["meter"],
                                    amount=held["amount"],
                                    run_id=held.get("run_id"),
                                    held_at=held.get("held_at"),
                                )
                            )
                        for entry in parts["history"][-GRANT_HISTORY_KEPT:]:
                            session.add(
                                StorageBudgetGrant(
                                    key=key,
                                    meter=entry.get("meter"),
                                    amount=float(entry.get("amount") or 0.0),
                                    approver=entry.get("approver"),
                                    note=entry.get("note"),
                                    granted_at=entry.get("granted_at"),
                                )
                            )
                    self._commit(session)
                except Exception:
                    self._rollback_quietly(session)
                    raise
                self._legacy_budgets_moved.add(key)
        finally:
            self._release_session(session)

    async def save_budget_state(self, state: dict, expected_version: int | None) -> int:
        from sqlalchemy.exc import IntegrityError

        from omnicoreagent.core.runs import RunStateConflict

        key = state["key"]
        version = (expected_version or 0) + 1
        data = json.dumps({**state, "version": version}, default=str)
        # A document written now has not been looked at yet.
        self._legacy_budgets_moved.discard(key)
        self._legacy_budgets_gone = False

        def _save() -> int:
            session = self._get_session()
            try:
                if expected_version is None:
                    session.add(StorageBudgetState(key=key, version=version, data=data))
                    try:
                        self._commit(session)
                    except IntegrityError:
                        session.rollback()
                        raise RunStateConflict(f"Budget {key} already exists") from None
                    return version
                result = session.execute(
                    update(StorageBudgetState)
                    .where(
                        StorageBudgetState.key == key,
                        StorageBudgetState.version == expected_version,
                    )
                    .values(version=version, data=data)
                )
                self._commit(session)
                if result.rowcount != 1:
                    raise RunStateConflict(
                        f"Budget {key} changed since version {expected_version}"
                    )
                return version
            finally:
                self._release_session(session)

        return await self._in_pool(self._with_retry, _save)
