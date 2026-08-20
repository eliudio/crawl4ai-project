"""
Unit tests for admin/export/csv_export.py.

No real database anywhere here: `_fetch_rows` (the one function that actually
queries) and `session_scope` are monkeypatched with an in-memory object graph
built directly from the ORM classes (Event/EventDistance/RaceType/Organiser
work fine as plain Python objects without a session - see common/models) - same
"monkeypatch at the real seam" style as test_scraping.py, rather than standing
up a real/in-memory-SQLite database (which won't work here anyway: Organiser
uses Postgres' ARRAY column type, which SQLite can't build).
"""

import csv
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from services.admin.export import csv_export
from services.common.models import (
    Event,
    EventDistance,
    EventLifecycle,
    EventStatus,
    Occurrence,
    RaceType,
    RegistrationStatus,
    Sport,
)

# Captured before any test's autouse fixture monkeypatches csv_export._fetch_rows (see
# _no_real_db below) - the tests that need the REAL query-building logic call this
# directly instead of the (by-then-patched) module attribute.
_real_fetch_rows = csv_export._fetch_rows


# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------

def test_format_distance_with_price_and_race_type():
    d = EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k"))
    assert csv_export._format_distance(d) == "5K [running_5k]: £15"


def test_format_distance_no_price():
    d = EventDistance(distance_text="Fun Run", price_text=None, race_type=None)
    assert csv_export._format_distance(d) == "Fun Run"


def test_format_distance_no_race_type_but_has_price():
    d = EventDistance(distance_text="10K", price_text="£20", race_type=None)
    assert csv_export._format_distance(d) == "10K: £20"


def test_distances_summary_joins_multiple_with_semicolon():
    event = Event(distances=[
        EventDistance(distance_text="5K", price_text="£15", race_type=None),
        EventDistance(distance_text="10K", price_text="£20", race_type=None),
    ])
    assert csv_export._distances_summary(event) == "5K: £15; 10K: £20"


def test_distances_summary_empty_when_no_distances():
    assert csv_export._distances_summary(Event(distances=[])) == ""


# ---------------------------------------------------------------------------
# _fetch_rows - checks the actual query it builds, without needing a real
# database: a fake Session.execute just captures the statement instead of
# running it, then we inspect its compiled SQL text directly.
# ---------------------------------------------------------------------------

class _CapturingSession:
    def __init__(self):
        self.captured_statement = None

    def execute(self, stmt):
        self.captured_statement = stmt
        return []


def test_fetch_rows_excludes_invalid_events():
    # No export format should have to remember to filter these out itself - see
    # the reported case, e.g. runthrough.co.uk's redirect-only "Running Tours" events.
    session = _CapturingSession()
    _real_fetch_rows(session)
    compiled = str(session.captured_statement)
    assert "events.status = " in compiled


def test_fetch_rows_still_filters_by_organiser_id_when_given():
    session = _CapturingSession()
    _real_fetch_rows(session, organiser_id=7)
    compiled = str(session.captured_statement)
    assert "events.organiser_id = " in compiled
    assert "events.status = " in compiled  # both filters present, not one replacing the other


def test_fetch_rows_can_request_invalid_status_instead():
    session = _CapturingSession()
    _real_fetch_rows(session, status=EventStatus.INVALID)
    # Render with literal_binds so the actual bound value (not just a ":status_1"
    # placeholder) shows up in the compiled text - confirms it's really INVALID, not
    # still defaulting to VALID.
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "'INVALID'" in compiled


def test_fetch_rows_status_none_omits_the_filter_entirely():
    session = _CapturingSession()
    _real_fetch_rows(session, status=None)
    compiled = str(session.captured_statement)
    # events.status still appears as a plain selected column either way - the thing
    # that must be absent is a WHERE clause filtering on it at all.
    assert "WHERE" not in compiled
    assert "events.status = " not in compiled


# ---------------------------------------------------------------------------
# export_csv - _fetch_rows and session_scope monkeypatched, so no real
# database is ever touched.
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_rows():
    """Two organisers, three events, mirroring what _fetch_rows would return."""
    event1 = Event(
        id=1, organiser_id=1, url="https://acme.example/5k", name="Acme 5K", sport="running",
        status=EventStatus.VALID, date_text="Sunday", location="Acme Park", raw_markdown=None,
        distances=[EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k"))],
    )
    event2 = Event(
        id=2, organiser_id=1, url="https://acme.example/10k", name="Acme 10K", sport="running",
        status=EventStatus.VALID, date_text="Sunday", location="Acme Park", raw_markdown=None,
        distances=[
            EventDistance(distance_text="10K", price_text="£20", race_type=RaceType(label="running_10k", sport=Sport.RUNNING, distance_category="10k")),
            EventDistance(distance_text="Fun Run", price_text=None, race_type=None),
        ],
    )
    event3 = Event(
        id=3, organiser_id=2, url="https://beta.example/tri", name="Beta Triathlon", sport="triathlon",
        status=EventStatus.VALID, date_text="Saturday", location="Beta Lake", raw_markdown=None,
        distances=[EventDistance(distance_text="Sprint Triathlon", price_text="£50", race_type=RaceType(label="triathlon_sprint_triathlon", sport=Sport.TRIATHLON, distance_category="sprint_triathlon"))],
    )
    return [
        (event1, "Acme Runners"),
        (event2, "Acme Runners"),
        (event3, "Beta Multisport"),
    ]


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch, sample_rows):
    """Every test in this module gets the same canned rows - no real DB/session ever touched."""

    @contextmanager
    def fake_session_scope():
        yield None  # never touched: _fetch_rows below ignores it entirely

    monkeypatch.setattr(csv_export, "session_scope", fake_session_scope)
    monkeypatch.setattr(csv_export, "_fetch_rows", lambda session, organiser_id=None, status=None: sample_rows)


def test_export_csv_writes_header_and_rows(tmp_path):
    output_path = tmp_path / "events.csv"
    count = csv_export.export_csv(output_path)

    assert count == 3
    with output_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 3
    assert rows[0]["name"] == "Acme 5K"
    assert rows[0]["organiser_name"] == "Acme Runners"
    assert rows[0]["distances"] == "5K [running_5k]: £15"
    # event2 has one categorised and one uncategorised distance - both show up.
    assert rows[1]["distances"] == "10K [running_10k]: £20; Fun Run"
    assert rows[0]["status"] == "valid"
    assert rows[0]["invalid_reason"] == ""


def test_export_csv_includes_occurrence_and_coordinates(monkeypatch, tmp_path):
    event = Event(
        id=5, organiser_id=1, url="https://parkrun.example/bushy", name="Bushy parkrun",
        sport="running", status=EventStatus.VALID, date_text="Every Saturday, 9:00am",
        occurrence=Occurrence.WEEKLY, occurrence_weekdays=["sat"],
        occurrence_time=None, occurrence_starts_on=None, occurrence_ends_on=None,
        # parkrun is the canonical no-registration-needed case - see RegistrationStatus's
        # own docstring.
        registration_status=RegistrationStatus.NOT_REQUIRED,
        latitude=51.4118, longitude=-0.3277,
        location=None, raw_markdown=None, distances=[], occurrences=[],
    )
    monkeypatch.setattr(csv_export, "_fetch_rows", lambda session, organiser_id=None, status=None: [(event, "parkrun UK")])

    csv_export.export_csv(tmp_path / "parkrun.csv")

    with (tmp_path / "parkrun.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["occurrence"] == "weekly"
    assert rows[0]["occurrence_weekdays"] == "sat"
    assert rows[0]["latitude"] == "51.4118"
    assert rows[0]["longitude"] == "-0.3277"
    assert rows[0]["registration_status"] == "not_required"


def test_export_csv_includes_registration_status_and_dates(monkeypatch, tmp_path):
    # zigzagrunning.co.uk's Two Hundred Miles Challenge - states outright "Registration is
    # Closed" with no opening/closing date given at all (see RegistrationStatus's own
    # docstring for why 'unknown' rather than 'open' is the safe default when nothing's said,
    # in contrast to this - a page that DOES say something, just not a date).
    closed_event = Event(
        id=6, organiser_id=1, url="https://www.zigzagrunning.co.uk/event-details/two-hundred-miles-challenge",
        name="Two Hundred Miles Challenge", sport="running", status=EventStatus.VALID,
        registration_status=RegistrationStatus.CLOSED, registration_text="Registration is Closed",
        registration_opens_at=None, registration_closes_at=None,
        location=None, raw_markdown=None, distances=[], occurrences=[],
    )
    open_event = Event(
        id=7, organiser_id=1, url="https://example.org/event/some-10k",
        name="Some 10k", sport="running", status=EventStatus.VALID,
        registration_status=RegistrationStatus.OPEN,
        registration_text="Entries open 1 March, close 30 June 2026 23:59",
        registration_opens_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
        registration_closes_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
        location=None, raw_markdown=None, distances=[], occurrences=[],
    )
    monkeypatch.setattr(
        csv_export, "_fetch_rows",
        lambda session, organiser_id=None, status=None: [(closed_event, "ZigZag Running"), (open_event, "Acme Runners")],
    )

    csv_export.export_csv(tmp_path / "registration.csv")

    with (tmp_path / "registration.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["registration_status"] == "closed"
    assert rows[0]["registration_text"] == "Registration is Closed"
    assert rows[0]["registration_opens_at"] == ""
    assert rows[0]["registration_closes_at"] == ""

    assert rows[1]["registration_status"] == "open"
    assert rows[1]["registration_opens_at"] == str(datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
    assert rows[1]["registration_closes_at"] == str(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc))


def test_export_csv_includes_lifecycle_status_and_text(monkeypatch, tmp_path):
    cancelled_event = Event(
        id=8, organiser_id=1, url="https://example.org/event/storm-10k",
        name="Storm-hit 10k", sport="running", status=EventStatus.VALID,
        lifecycle_status=EventLifecycle.CANCELLED, lifecycle_text="Cancelled due to adverse weather",
        location=None, raw_markdown=None, distances=[], occurrences=[],
    )
    scheduled_event = Event(
        id=9, organiser_id=1, url="https://example.org/event/some-10k",
        name="Some 10k", sport="running", status=EventStatus.VALID,
        lifecycle_status=EventLifecycle.SCHEDULED, lifecycle_text=None,
        location=None, raw_markdown=None, distances=[], occurrences=[],
    )
    monkeypatch.setattr(
        csv_export, "_fetch_rows",
        lambda session, organiser_id=None, status=None: [(cancelled_event, "Acme Runners"), (scheduled_event, "Acme Runners")],
    )

    csv_export.export_csv(tmp_path / "lifecycle.csv")

    with (tmp_path / "lifecycle.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["lifecycle_status"] == "cancelled"
    assert rows[0]["lifecycle_text"] == "Cancelled due to adverse weather"
    assert rows[1]["lifecycle_status"] == "scheduled"
    assert rows[1]["lifecycle_text"] == ""


def test_export_csv_includes_invalid_event_status_and_reason(monkeypatch, tmp_path):
    invalid_event = Event(
        id=99, organiser_id=1, url="https://acme.example/redirect", name="No event details available",
        sport="other", status=EventStatus.INVALID,
        invalid_reason="Page is just a redirect notice to an external site, no event details shown",
        date_text=None, location=None, raw_markdown=None, distances=[],
    )
    monkeypatch.setattr(csv_export, "_fetch_rows", lambda session, organiser_id=None, status=None: [(invalid_event, "Acme Runners")])

    csv_export.export_csv(tmp_path / "invalid.csv")

    with (tmp_path / "invalid.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["status"] == "invalid"
    assert rows[0]["invalid_reason"] == "Page is just a redirect notice to an external site, no event details shown"
