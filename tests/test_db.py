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
from services.models import Base, Event, Organiser


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


def test_not_null_column_with_a_server_default_is_added_and_backfilled():
    # Contrast with the test above: a NOT NULL column CAN be safely auto-added when it
    # carries a server_default - existing rows get backfilled with that value, same as
    # a real migration would (confirmed needed in practice: Event.occurrence).
    engine = _make_engine()

    old_metadata = MetaData()
    Table("widgets", old_metadata, Column("id", Integer, primary_key=True))
    old_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO widgets (id) VALUES (1)")

    new_metadata = MetaData()
    Table(
        "widgets", new_metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(20), nullable=False, server_default="active"),
    )

    db._add_missing_columns(engine, metadata=new_metadata)

    with engine.connect() as conn:
        status = conn.exec_driver_sql("SELECT status FROM widgets WHERE id = 1").scalar()
    assert status == "active"


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


def test_regression_repeating_events_columns_and_table_added_to_preexisting_db():
    # Same incident class as the summary_alt/summary_short one above, replayed for the
    # repeating-events feature: an `events` table (and a whole database) that predates
    # occurrence/occurrence_weekdays/occurrence_time/occurrence_starts_on/
    # occurrence_ends_on/latitude/longitude, and never had an event_occurrences table
    # at all, must gain all of it - both new columns on an existing table (handled by
    # _add_missing_columns) and a brand new table (handled by create_all itself, which
    # init_db() always calls first).
    engine = _make_engine()

    new_occurrence_columns = {
        "occurrence", "occurrence_weekdays", "occurrence_time",
        "occurrence_starts_on", "occurrence_ends_on", "latitude", "longitude",
    }
    pre_existing_metadata = MetaData()
    old_columns = [
        Column(c.name, c.type, nullable=c.nullable)
        for c in Event.__table__.columns
        if c.name not in new_occurrence_columns
    ]
    Table("events", pre_existing_metadata, *old_columns)
    pre_existing_metadata.create_all(engine)
    assert not new_occurrence_columns & {c["name"] for c in inspect(engine).get_columns("events")}
    assert "event_occurrences" not in inspect(engine).get_table_names()

    # Mirrors init_db() exactly: create_all() for any table missing entirely
    # (event_occurrences - "events" itself already exists, so this is a no-op for
    # it, same as create_all() always was for the reported summary_alt incident too),
    # then _add_missing_columns() for columns missing from an already-existing one.
    # Restricted to just this one new table rather than the real init_db()'s full
    # Base.metadata.create_all(engine) - that would also try (and fail) to build
    # Organiser's Postgres-only ARRAY column on SQLite, irrelevant to this test.
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["event_occurrences"]], checkfirst=True)
    db._add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert new_occurrence_columns <= columns
    assert "event_occurrences" in inspect(engine).get_table_names()


def test_regression_registration_columns_added_and_backfilled_on_preexisting_table():
    # Same incident class again, for the registration-status feature: registration_status
    # is NOT NULL with only a Python-side default= (same shape Event.occurrence needed a
    # server_default for above) - an `events` table that predates it must gain it (and its
    # sibling nullable columns) with existing rows backfilled to 'unknown', not break.
    engine = _make_engine()

    new_columns = {
        "registration_status", "registration_text",
        "registration_opens_at", "registration_closes_at",
    }
    pre_existing_metadata = MetaData()
    old_columns = [
        Column(c.name, c.type, nullable=c.nullable)
        # lifecycle_status is also NOT NULL with no server_default carried over by this
        # reconstruction (see below) - excluded here too so this test's own INSERT (which
        # predates the registration feature, not just lifecycle) doesn't trip over it.
        for c in Event.__table__.columns
        if c.name not in new_columns and c.name not in ("lifecycle_status", "lifecycle_text")
    ]
    Table("events", pre_existing_metadata, *old_columns)
    pre_existing_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO events (id, organiser_id, url, status, occurrence, first_seen_at, last_seen_at) "
            "VALUES (1, 1, 'https://example.org/event/pre-existing', 'valid', 'one_off', "
            "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    assert not new_columns & {c["name"] for c in inspect(engine).get_columns("events")}

    db._add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert new_columns <= columns
    with engine.connect() as conn:
        status = conn.exec_driver_sql("SELECT registration_status FROM events WHERE id = 1").scalar()
    assert status == "unknown"


def test_regression_lifecycle_columns_added_and_backfilled_on_preexisting_table():
    # Same incident class again, for the lifecycle-status feature (cancelled/postponed):
    # lifecycle_status is NOT NULL with only a Python-side default= - an `events` table that
    # predates it must gain it (and its sibling nullable column) with existing rows
    # backfilled to 'scheduled', not break.
    engine = _make_engine()

    new_columns = {"lifecycle_status", "lifecycle_text"}
    pre_existing_metadata = MetaData()
    old_columns = [
        Column(c.name, c.type, nullable=c.nullable)
        for c in Event.__table__.columns
        if c.name not in new_columns
    ]
    Table("events", pre_existing_metadata, *old_columns)
    pre_existing_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO events (id, organiser_id, url, status, occurrence, registration_status, "
            "first_seen_at, last_seen_at) VALUES (1, 1, 'https://example.org/event/pre-existing', "
            "'valid', 'one_off', 'unknown', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    assert not new_columns & {c["name"] for c in inspect(engine).get_columns("events")}

    db._add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert new_columns <= columns
    with engine.connect() as conn:
        status = conn.exec_driver_sql("SELECT lifecycle_status FROM events WHERE id = 1").scalar()
    assert status == "scheduled"


def test_regression_organiser_handler_added_and_backfilled_on_preexisting_table():
    # Same incident class again, for Organiser.handler specifically: it's NOT NULL
    # with only a Python-side default= (same shape Event.occurrence needed a
    # server_default for above) - confirmed needed in practice when custom_handler
    # became the always-present, non-nullable `handler` column.
    engine = _make_engine()

    new_columns = {"handler", "handler_params"}
    pre_existing_metadata = MetaData()
    old_columns = [
        # listing_urls (Postgres ARRAY) excluded too - same SQLite limitation as
        # everywhere else in this file; irrelevant to what this test checks.
        Column(c.name, c.type, nullable=c.nullable)
        for c in Organiser.__table__.columns
        if c.name not in new_columns and c.name != "listing_urls"
    ]
    Table("organisers", pre_existing_metadata, *old_columns)
    pre_existing_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO organisers (id, name, homepage_url, source_type, active, created_at, updated_at) "
            "VALUES (1, 'Acme', 'https://acme.example/', 'organiser', 1, '2026-01-01', '2026-01-01')"
        )

    db._add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("organisers")}
    assert "handler" in columns
    assert "handler_params" in columns
    with engine.connect() as conn:
        handler = conn.exec_driver_sql("SELECT handler FROM organisers WHERE id = 1").scalar()
    assert handler == "default"


def test_init_db_calls_add_missing_columns(monkeypatch):
    # The actual incident happened via local_runner.py -> init_db() - a unit test
    # against _add_missing_columns alone wouldn't have caught init_db() failing to
    # actually call it.
    calls = []
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda engine: calls.append("create_all"))
    monkeypatch.setattr(db, "_add_missing_columns", lambda engine: calls.append("_add_missing_columns"))

    db.init_db()

    assert calls == ["create_all", "_add_missing_columns"]
