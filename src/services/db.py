"""SQLAlchemy engine/session setup, shared by the HTTP app, workers, and CLI scripts."""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import DDL

from services.config import settings
from services.models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _add_missing_columns(engine: Engine, metadata: MetaData = Base.metadata) -> None:
    """
    Adds any column a model gained since its table was first created - the gap
    Base.metadata.create_all() leaves (see init_db()'s own docstring: it only
    creates tables that don't exist yet at all, never alters an already-existing
    one to add a column). Confirmed in practice: adding Event.summary_alt/
    summary_short to models.py worked fine against a fresh DB, but broke every
    query against organiser 57's (Three Forts Challenge) already-existing
    `events` table with psycopg's UndefinedColumn - a plain rerun after a `git
    pull` shouldn't require remembering to hand-run an ALTER TABLE first.

    Deliberately narrow, not a real migration tool: only ever ADDs a column,
    never renames/drops/alters an existing one, and only when the new column
    is nullable - a NOT NULL column with no server-side default can't be safely
    backfilled onto rows that already exist (there's no value to put in them),
    so that case is logged and skipped rather than guessed at; it still needs a
    real migration. Tables that don't exist in the database at all yet are left
    alone entirely - create_all() (called right before this) already handles
    those, columns and all.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                if not column.nullable:
                    print(
                        f"WARNING: {table.name}.{column.name} is missing from the database and is "
                        "NOT NULL with no default - can't auto-add it (existing rows would violate "
                        "the constraint). Add it manually."
                    )
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(DDL(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'))
                print(f"DEBUG added missing column {table.name}.{column.name} ({col_type})")


def init_db() -> None:
    """
    Create tables that don't exist yet, and add any column a model gained since
    its table was first created (see _add_missing_columns) - fine for phase 1;
    switch to Alembic once the schema stabilizes.
    """
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
