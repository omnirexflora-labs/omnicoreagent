"""Creating a shared database's tables when several processes start at once.

Scale plan, S4. SQLAlchemy's ``create_all`` looks for each table and then
creates the ones it did not find. Two processes starting together both look,
both find nothing, and both create — and on PostgreSQL the loser does not get a
polite "already exists" but an integrity error against ``pg_type``, which took
the whole process down at startup. The first two server processes brought up on
one database hit it immediately.

Losing that race is not a failure: what the loser wanted is what the winner
did. So the create is attempted, and if it fails the tables are looked for
again — if they are all there now, the process carries on.
"""

from __future__ import annotations

import time
from typing import Any

# How long to wait before looking again, in case the winner is mid-create.
_PAUSE_SECONDS = 0.2


def create_tables(engine: Any, metadata: Any, *, attempts: int = 3) -> None:
    """Create ``metadata``'s tables, tolerating another process creating them.

    Whatever the database raised is raised if the tables are still not there.
    """
    from sqlalchemy import inspect

    last: Exception | None = None
    for attempt in range(max(attempts, 1)):
        try:
            metadata.create_all(engine)
            return
        except Exception as exc:  # the database's own error, re-raised below
            last = exc
        if _tables_exist(engine, metadata, inspect):
            return
        if attempt + 1 < attempts:
            time.sleep(_PAUSE_SECONDS)
    raise last if last is not None else RuntimeError("Could not create tables")


def _tables_exist(engine: Any, metadata: Any, inspect: Any) -> bool:
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            return all(
                inspector.has_table(table.name) for table in metadata.sorted_tables
            )
    except Exception:
        return False
