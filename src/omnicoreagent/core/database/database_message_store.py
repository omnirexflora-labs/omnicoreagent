import json
from datetime import datetime, timezone
from typing import Any
import uuid
import threading
from sqlalchemy import String, Text, DateTime, create_engine, func, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.mutable import MutableDict
from omnicoreagent.core.utils import logger

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
        # Only initialize once due to singleton pattern
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
                    # Configure connection pool settings
                    connection_kwargs = {
                        "pool_size": 20,  # Connection pool size
                        "max_overflow": 30,  # Max overflow connections
                        "pool_timeout": 30,  # Connection timeout
                        "pool_recycle": 1800,  # Recycle connections after 30 minutes
                        "pool_pre_ping": True,  # Test connections before use
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

            # Fresh session for background processing (not pooled so it wont share context with the main event)
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


# Global SQL connection manager - created only when needed
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

    # ISO 8601 string for portability with different database
    timestamp: Mapped[str] = mapped_column(
        String(50), default=lambda: datetime.now(timezone.utc).isoformat()
    )
    msg_metadata: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(DynamicJSON), default={}
    )


class DatabaseMessageStore:
    """
    Database-backed message store for storing, retrieving, and clearing messages by session.
    """

    def __init__(self, db_url: str = None, **kwargs: Any):
        # Set up basic configuration
        self.db_url = db_url
        self.memory_config: dict[str, Any] = {}

        # Only initialize database connections if db_url is provided
        if db_url:
            # Use SQL connection manager for optimized connection pooling
            self._sql_manager = get_sql_manager()
            self._sql_manager.initialize(db_url, **kwargs)

            # Get engine for table inspection and creation
            db_engine = self._sql_manager.get_engine()

            # Check if tables exist before creating them
            inspector = inspect(db_engine)
            existing_tables = inspector.get_table_names()

            if "messages" not in existing_tables:
                Base.metadata.create_all(db_engine)

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

            # Get engine for table inspection and creation
            db_engine = self._sql_manager.get_engine()

            # Check if tables exist before creating them
            inspector = inspect(db_engine)
            existing_tables = inspector.get_table_names()

            if "messages" not in existing_tables:
                Base.metadata.create_all(db_engine)

            logger.debug("DatabaseMessageStore connection initialized")

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
                # Only release pooled sessions, fresh sessions are not pooled
                if not hasattr(session, "_is_fresh_session"):
                    self._sql_manager.release_session()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")

    def set_memory_config(self, mode: str, value: int = None) -> None:
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )
        self.memory_config = {"mode": mode, "value": value}

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str = None,
    ) -> None:
        session = None
        try:
            if metadata is None:
                metadata = {}
            session = self._get_session(
                fresh_for_background=False
            )  # Use pooled session
            message = StorageMessage(
                session_id=session_id,
                role=role,
                content=content,
                msg_metadata=metadata,
            )
            session.add(message)
            session.commit()
            logger.debug(f"Stored message for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to store message: {e}")
        finally:
            self._release_session(session)

    async def get_messages(
        self, session_id: str = None, agent_name: str | None = None
    ) -> list[dict[str, Any]]:
        session = None
        try:
            session = self._get_session(fresh_for_background=False)

            query = session.query(StorageMessage)

            if session_id:
                query = query.filter(StorageMessage.session_id == session_id)

            if agent_name:
                query = query.filter(
                    StorageMessage.msg_metadata.contains({"agent_name": agent_name})
                )

            messages = query.order_by(StorageMessage.timestamp.asc()).all()
            result = [
                {
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

            # Apply memory configuration optimizations
            mode = self.memory_config.get("mode", "token_budget")
            value = self.memory_config.get("value")
            if mode.lower() == "sliding_window" and value is not None:
                result = result[-value:]
            elif mode.lower() == "token_budget" and value is not None:
                total_tokens = sum(len(str(msg["content"]).split()) for msg in result)
                while total_tokens > value and result:
                    result.pop(0)
                    total_tokens = sum(
                        len(str(msg["content"]).split()) for msg in result
                    )

            return result
        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []
        finally:
            self._release_session(session)

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        session = None
        try:
            session = self._get_session(fresh_for_background=False)

            if session_id and agent_name:
                # Clear messages for specific agent in specific session
                query = session.query(StorageMessage).filter(
                    StorageMessage.session_id == session_id,
                    StorageMessage.msg_metadata.contains({"agent_name": agent_name}),
                )
                query.delete()
            elif session_id:
                # Clear all messages for specific session
                query = session.query(StorageMessage).filter(
                    StorageMessage.session_id == session_id
                )
                query.delete()
            elif agent_name:
                # Clear messages for specific agent across all sessions
                query = session.query(StorageMessage).filter(
                    StorageMessage.msg_metadata.contains({"agent_name": agent_name})
                )
                query.delete()
            else:
                # Clear all messages
                session.query(StorageMessage).delete()

            session.commit()
            logger.debug(
                f"Cleared memory for session_id={session_id}, agent_name={agent_name}"
            )

        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
        finally:
            self._release_session(session)
