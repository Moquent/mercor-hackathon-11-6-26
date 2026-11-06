"""Engine, bounded startup retry, schema creation and append-only enforcement."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.database.models import Base

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=10, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

_APPEND_ONLY_DDL = (
    """CREATE OR REPLACE FUNCTION dagscope_reject_mutation() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION USING MESSAGE = 'observability tables are append-only: ' || TG_OP || ' on ' || TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS events_append_only ON events",
    """CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
       FOR EACH ROW EXECUTE FUNCTION dagscope_reject_mutation()""",
    "DROP TRIGGER IF EXISTS deps_append_only ON event_dependencies",
    """CREATE TRIGGER deps_append_only BEFORE UPDATE OR DELETE ON event_dependencies
       FOR EACH ROW EXECUTE FUNCTION dagscope_reject_mutation()""",
)


def wait_for_database(timeout_s: float = 60.0) -> None:
    """Bounded retry for the Postgres startup race."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def init_db() -> None:
    wait_for_database()
    with engine.begin() as conn:
        # Serialise concurrent initialisers.
        conn.execute(text("SELECT pg_advisory_xact_lock(727274)"))
        Base.metadata.create_all(conn)
        for statement in _APPEND_ONLY_DDL:
            conn.exec_driver_sql(statement)


@contextmanager
def transaction() -> Iterator[Session]:
    """A real DB transaction: commits on clean exit, rolls back on exception."""
    with SessionLocal() as session, session.begin():
        yield session


def current_xid(session: Session) -> str:
    """The real PostgreSQL transaction id, so commit status can be re-checked via pg_xact_status."""
    return str(session.execute(text("SELECT pg_current_xact_id()")).scalar_one())


if __name__ == "__main__":
    init_db()
    print("schema ready")
