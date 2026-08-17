"""
Unit tests for db.py's _add_missing_columns - the fix for the reported
incident: adding Event.summary_alt/summary_short to models.py worked fine
against a fresh database, but broke every query against organiser 57 (Three
Forts Challenge)'s already-existing `events` table with psycopg's
UndefinedColumn, since Base.metadata.create_all() only creates tables that
don't exist yet - it never alters an already-existing one to add a new column.

Real SQLite engines throughout (not mocked) - this is exactly the kind of
thing that's only meaningful against a real schema/real DDL, and SQLite's
ALTER TABLE ... ADD COLUMN support is what _add_missing_columns relies on.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect

from services import db
from services.models import Event


def _make_engine():
    return create_engine("sqlite:///:memory:")


def test_adds_a_missing_nullable_column_to_an_existing_table():
    engine = _make_engine()

    old_metadata = MetaData()
    Table("widgets", old_metadata, Column("id", Integer, primary_key=True), Column("name", String(50)))
    old_metadata.create_all(engine)

    new_metadata = MetaData()
    Table(
        "widgets", new_metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
        Column("description", String(200), nullable=True),
    )

    db._add_missing_columns(engine, metadata=new_metadata)

    columns = {c["name"] for c in inspect(engine).get_columns("widgets")}
    assert "description" in columns


def test_skips_tables_that_dont_exist_in_the_database_yet():
    # create_all() (called right before this in init_db()) already handles a brand
    # new table, columns and all - _add_missing_columns must not try (and fail)
    # to ALTER a table that was never created in the first place.
    engine = _make_engine()
    metadata = MetaData()
    Table("brand_new", metadata, Column("id", Integer, primary_key=True))

    db._add_missing_columns(engine, metadata=metadata)  # must not raise

    assert "brand_new" not in inspect(engine).get_table_names()


def test_not_null_column_with_no_default_is_skipped_and_warned(capsys):
    engine = _make_engine()

    old_metadata = MetaData()
    Table("widgets", old_metadata, Column("id", Integer, primary_key=True))
    old_metadata.create_all(engine)

    new_metadata = MetaData()
    Table(
        "widgets", new_metadata,
        Column("id", Integer, primary_key=True),
        Column("required_field", String(50), nullable=False),
    )

    db._add_missing_columns(engine, metadata=new_metadata)

    columns = {c["name"] for c in inspect(engine).get_columns("widgets")}
    assert "required_field" not in columns
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "widgets.required_field" in out


def test_existing_columns_are_left_untouched_and_call_is_idempotent():
    engine = _make_engine()
    metadata = MetaData()
    Table("widgets", metadata, Column("id", Integer, primary_key=True), Column("name", String(50)))
    metadata.create_all(engine)

    db._add_missing_columns(engine, metadata=metadata)  # nothing missing - must not error
    db._add_missing_columns(engine, metadata=metadata)  # calling it again must still not error

    columns = {c["name"] for c in inspect(engine).get_columns("widgets")}
    assert columns == {"id", "name"}


def test_regression_summary_alt_and_summary_short_added_to_preexisting_events_table():
    # The exact reported incident, reproduced directly against the real Event model:
    # an `events` table that predates summary_alt/summary_short (i.e. was created by
    # an older version of models.py) must gain both columns, not break every query
    # against it with "column events.summary_alt does not exist".
    engine = _make_engine()

    pre_existing_metadata = MetaData()
    old_columns = [
        Column(c.name, c.type, nullable=c.nullable)
        for c in Event.__table__.columns
        if c.name not in ("summary_alt", "summary_short")
    ]
    Table("events", pre_existing_metadata, *old_columns)
    pre_existing_metadata.create_all(engine)
    assert "summary_alt" not in {c["name"] for c in inspect(engine).get_columns("events")}

    db._add_missing_columns(engine)  # default metadata=Base.metadata, real Event table

    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert "summary_alt" in columns
    assert "summary_short" in columns


def test_init_db_calls_add_missing_columns(monkeypatch):
    # The actual incident happened via local_runner.py -> init_db() - a unit test
    # against _add_missing_columns alone wouldn't have caught init_db() failing to
    # actually call it.
    calls = []
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda engine: calls.append("create_all"))
    monkeypatch.setattr(db, "_add_missing_columns", lambda engine: calls.append("_add_missing_columns"))

    db.init_db()

    assert calls == ["create_all", "_add_missing_columns"]
