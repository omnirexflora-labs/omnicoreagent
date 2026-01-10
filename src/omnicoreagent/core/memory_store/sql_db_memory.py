import json
from datetime import datetime, timezone
from typing import Any, Callable
import uuid
import threading
import asyncio
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from sqlalchemy import String, Text, DateTime, create_engine, func, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.mutable import MutableDict
from omnicoreagent.core.utils import logger
from omnicoreagent.core.utils import utc_now_str
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,
)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig

DEFAULT_MAX_KEY_LENGTH = 128
DEFAULT_MAX_VARCHAR_LENGTH = 256


class SQLConnectionManager:
    """
    SQL connection manager for efficient session management and connection pooling.
    """

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._engine = None
            self._session_factory = None
            self._session_count = 0
            logger.debug("SQLConnectionManager initialized (singleton)")

    def initialize(self, db_url: str, **kwargs):
        """Initialize the SQL engine and session factory."""
        with self._lock:
            if self._engine is None:
                try:
                    connection_kwargs = {
                        "pool_size": 20,
                        "max_overflow": 30,
                        "pool_timeout": 30,
                        "pool_recycle": 1800,
                        "pool_pre_ping": True,
                        **kwargs,
                    }

                    self._engine = create_engine(db_url, **connection_kwargs)
                    self._session_factory = sessionmaker(bind=self._engine)

                    logger.debug(f"[SQLManager] Created SQL connection pool: {db_url}")

                except Exception as e:
                    logger.error(f"[SQLManager] Failed to create SQL engine: {e}")
                    raise

    def get_session(self):
        """Get a database session from the pool."""
        with self._lock:
            if self._session_factory is None:
                raise RuntimeError(
                    "SQLConnectionManager not initialized. Call initialize() first."
                )

            self._session_count += 1
            logger.debug(f"[SQLManager] SQL session usage count: {self._session_count}")
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

    def close_all(self):
        """Close all connections."""
        with self._lock:
            if self._engine:
                self._engine.dispose()
                self._engine = None
                self._session_factory = None
                self._session_count = 0
                logger.debug("[SQLManager] Closed all SQL connections")


_sql_manager = None


def get_sql_manager():
    """Get the global SQL connection manager instance."""
    global _sql_manager
    if _sql_manager is None:
        _sql_manager = SQLConnectionManager()
    return _sql_manager


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

    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )
    inactive_reason: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    summary_id: Mapped[str | None] = mapped_column(
        String(DEFAULT_MAX_KEY_LENGTH), nullable=True, default=None
    )


class DatabaseMessageStore(AbstractMemoryStore):
    """
    Database-backed message store for storing, retrieving, and clearing messages by session.
    """

    def __init__(self, db_url: str = None, **kwargs: Any):
        self.db_url = db_url
        self.memory_config: dict[str, Any] = {}
        self.summary_config: dict[str, Any] = {}
        self.summarize_fn: Callable = None

        if db_url:
            self._sql_manager = get_sql_manager()
            self._sql_manager.initialize(db_url, **kwargs)

            db_engine = self._sql_manager.get_engine()

            inspector = inspect(db_engine)
            existing_tables = inspector.get_table_names()

            if "messages" not in existing_tables:
                Base.metadata.create_all(db_engine)
            else:
                self._migrate_add_columns(db_engine, inspector)

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
                Base.metadata.create_all(db_engine)

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
                            text(f"ALTER TABLE messages ADD COLUMN {col_name} {col_def}")
                        )
                        conn.commit()
                        logger.debug(f"Added column '{col_name}' to messages table")
                    except Exception as e:
                        logger.debug(f"Column '{col_name}' may already exist: {e}")

    def _get_session(self, fresh_for_background: bool = False):
        """Get a database session from the connection manager."""
        if self._sql_manager is None:
            raise RuntimeError("Database not configured - no db_url provided")
        if fresh_for_background:
            return self._sql_manager.get_fresh_session()
        else:
            return self._sql_manager.get_session()

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

        def _store():
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
                session.commit()
            except Exception as e:
                logger.error(f"Failed to store message: {e}")
                if session:
                    session.rollback()
            finally:
                self._release_session(session)

        await asyncio.to_thread(_store)

    async def get_messages(
        self, session_id: str = None, agent_name: str | None = None
    ) -> list[dict[str, Any]]:
        
        def _fetch_messages():
            session = None
            try:
                session = self._get_session(fresh_for_background=False)
                query = session.query(StorageMessage).filter(
                    StorageMessage.status == "active"
                )

                if session_id:
                    query = query.filter(StorageMessage.session_id == session_id)

                if agent_name:
                    query = query.filter(
                        StorageMessage.msg_metadata.contains({"agent_name": agent_name})
                    )

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
            except Exception as e:
                logger.error(f"Failed to get messages: {e}")
                return []
            finally:
                self._release_session(session)

        result = await asyncio.to_thread(_fetch_messages)

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
                        logger.debug(f"Deleted {len(summarized_ids)} summarized messages")
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
        session = None
        try:
            session = self._get_session(fresh_for_background=False)

            if session_id and agent_name:
                query = session.query(StorageMessage).filter(
                    StorageMessage.session_id == session_id,
                    StorageMessage.msg_metadata.contains({"agent_name": agent_name}),
                )
                query.delete()
            elif session_id:
                query = session.query(StorageMessage).filter(
                    StorageMessage.session_id == session_id
                )
                query.delete()
            elif agent_name:
                query = session.query(StorageMessage).filter(
                    StorageMessage.msg_metadata.contains({"agent_name": agent_name})
                )
                query.delete()
            else:
                session.query(StorageMessage).delete()

            session.commit()
            logger.debug(
                f"Cleared memory for session_id={session_id}, agent_name={agent_name}"
            )

        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
        finally:
            self._release_session(session)

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

        def _mark():
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

                session.commit()

            except Exception as e:
                logger.error(f"Failed to mark messages as summarized: {e}")
                if session:
                    session.rollback()
            finally:
                self._release_session(session)

        await asyncio.to_thread(_mark)
