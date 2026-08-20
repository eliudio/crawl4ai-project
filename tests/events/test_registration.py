"""
Unit tests for events/registration.py: apply_fields (the extract_event_fields()-
shaped dict -> Event/EventDistance/EventOccurrence writer shared by both
pipelines) and register_event_from_fields (the structured-bulk-feed importers'
own direct-from-source-data upsert, built on top of apply_fields).

apply_fields is exercised directly here (construct an Event, call it, assert on
the mutated object) rather than through pattern_site/event_crawler.crawl_event -
see tests/pattern_site/test_event_crawler.py for crawl_event's own orchestration
(robots/hash-check/dead-link preflight) tested end-to-end instead.

Runs against a throwaway in-memory SQLite database - RaceType is the only table
apply_fields' own DB access touches (via events/race_types.get_or_create_race_type);
Event/CrawlRun/EventDistance/EventOccurrence are needed too for
register_event_from_fields's own upsert below.
"""

from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.common.models import (
    CrawlRun,
    Event,
    EventDistance,
    EventLifecycle,
    EventOccurrence,
    EventStatus,
    Occurrence,
    RaceType,
    RegistrationStatus,
)
from services.events import registration


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    for table in (Event.__table__, CrawlRun.__table__, EventDistance.__table__, EventOccurrence.__table__, RaceType.__table__):
        table.metadata.create_all(engine, tables=[table])
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """apply_fields calls rewrite_summary/geocode_event_location unconditionally
    (see its own docstring) - stubbed here so no test in this file makes a real,
    unmocked LLM/Nominatim call underneath it, unless it overrides one itself."""
    monkeypatch.setattr(registration, "rewrite_summary", lambda summary: {"summary_alt": None, "summary_short": None})
    monkeypatch.setattr(registration, "geocode_event_location", lambda location, start_location, finish_location: None)


# ---------------------------------------------------------------------------
# apply_fields - summary/summary_alt/summary_short
# ---------------------------------------------------------------------------

def test_summary_rewrite_populates_alt_and_short_when_summary_present(monkeypatch, session):
    captured = {}

    def fake_rewrite_summary(summary):
        captured["summary"] = summary
        return {"summary_alt": "Reworded coastal 10k.", "summary_short": "Coastal 10k."}

    monkeypatch.setattr(registration, "rewrite_summary", fake_rewrite_summary)

    event = Event(organiser_id=1, url="https://example.org/event/real")
    registration.apply_fields(session, event, {
        "name": "Real Event", "sport": "running", "distances": [],
        "summary": "A scenic 10k along the coast.",
    }, "bot")

    assert captured["summary"] == "A scenic 10k along the coast."
    assert event.summary == "A scenic 10k along the coast."
    assert event.summary_alt == "Reworded coastal 10k."
    assert event.summary_short == "Coastal 10k."


def test_summary_rewrite_skipped_when_no_summary(monkeypatch, session):
    def should_not_be_called(summary):
        raise AssertionError("should not call rewrite_summary with no summary to rewrite")

    monkeypatch.setattr(registration, "rewrite_summary", should_not_be_called)

    event = Event(organiser_id=1, url="https://example.org/event/real")
    registration.apply_fields(session, event, {"name": "Real Event", "sport": "running", "distances": []}, "bot")

    assert event.summary is None
    assert event.summary_alt is None
    assert event.summary_short is None


def test_known_summary_alt_and_short_used_as_is_skipping_the_rewrite_call(monkeypatch, session):
    # register_event_from_fields's own callers (e.g. feeds/parkrun_import.py) already
    # supply both - see apply_fields's own docstring for why that skips the LLM call
    # entirely rather than re-deriving them.
    def should_not_be_called(summary):
        raise AssertionError("should not call rewrite_summary when both are already supplied")

    monkeypatch.setattr(registration, "rewrite_summary", should_not_be_called)

    event = Event(organiser_id=1, url="https://www.parkrun.org.uk/bushy/")
    registration.apply_fields(session, event, {
        "name": "Bushy parkrun", "sport": "running", "distances": [],
        "summary": "Bushy parkrun", "summary_alt": "Bushy parkrun", "summary_short": "Bushy parkrun",
    }, "bot")

    assert event.summary_alt == "Bushy parkrun"
    assert event.summary_short == "Bushy parkrun"


# ---------------------------------------------------------------------------
# apply_fields - distances/occurrences tagged with the caller's registrator
# ---------------------------------------------------------------------------

def test_event_distance_and_occurrence_rows_tagged_with_registrator(session):
    event = Event(organiser_id=1, url="https://example.org/event/tagged")
    registration.apply_fields(session, event, {
        "name": "Tagged Event", "sport": "running",
        "distances": [{"distance_text": "10k", "price_text": "£20", "distance_category": "10k"}],
        "occurrence": "specific_dates",
        "occurrences": [{"date_text": "18th Aug 2026", "date_iso": "2026-08-18"}],
    }, "jane_doe")

    assert event.distances[0].registrator == "jane_doe"
    assert event.occurrences[0].registrator == "jane_doe"


# ---------------------------------------------------------------------------
# occurrence/occurrence_weekdays/occurrence_time/starts_on/ends_on and
# EventOccurrence rows - the repeating-events feature (see common/models's
# Occurrence docstring for the two mechanisms these split into).
# ---------------------------------------------------------------------------

def test_unbounded_recurrence_fields_populated_from_extraction(session):
    event = Event(organiser_id=1, url="https://example.org/event/parkrun")
    registration.apply_fields(session, event, {
        "name": "Village parkrun", "sport": "running", "distances": [],
        "occurrence": "weekly", "occurrences": [],
        "occurrence_weekdays": ["sat"], "occurrence_time": "09:00",
        "occurrence_starts_on": "2026-04-05", "occurrence_ends_on": "2026-09-30",
    }, "bot")

    assert event.occurrence == Occurrence.WEEKLY
    assert event.occurrence_weekdays == ["sat"]
    assert event.occurrence_time == time(9, 0)
    assert event.occurrence_starts_on == date(2026, 4, 5)
    assert event.occurrence_ends_on == date(2026, 9, 30)
    assert event.occurrences == []


def test_defaults_to_one_off_when_extraction_omits_occurrence(session):
    event = Event(organiser_id=1, url="https://example.org/event/plain")
    registration.apply_fields(session, event, {"name": "Plain Event", "sport": "running", "distances": []}, "bot")

    assert event.occurrence == Occurrence.ONE_OFF
    assert event.occurrence_weekdays is None
    assert event.occurrence_time is None


def test_specific_dates_create_event_occurrence_rows(session):
    event = Event(organiser_id=1, url="https://example.org/event/swim")
    registration.apply_fields(session, event, {
        "name": "Swim Sessions", "sport": "swimming", "distances": [],
        "occurrence": "specific_dates",
        "occurrences": [
            {"date_text": "18th Aug 2026", "date_iso": "2026-08-18", "time_text": "06:00 PM", "time_24h": "18:00", "price_text": "£10.00"},
            {"date_text": "20th Aug 2026", "date_iso": "2026-08-20", "time_text": None, "time_24h": None, "price_text": None},
        ],
    }, "bot")

    assert len(event.occurrences) == 2
    first, second = event.occurrences
    assert first.starts_at == datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)
    assert first.date_text == "18th Aug 2026"
    assert first.price_text == "£10.00"
    # No time stated for this one - midnight is a parsing placeholder, not a claim the
    # event actually starts at midnight.
    assert second.starts_at == datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    assert second.price_text is None


def test_occurrence_with_unparseable_date_iso_is_skipped_not_crashed(session):
    event = Event(organiser_id=1, url="https://example.org/event/swim2")
    registration.apply_fields(session, event, {
        "name": "Swim Sessions", "sport": "swimming", "distances": [],
        "occurrence": "specific_dates",
        "occurrences": [{"date_text": "some date", "date_iso": "not-a-real-date"}],
    }, "bot")

    assert event.occurrences == []


def test_reextraction_replaces_occurrences_not_appends(session):
    event = Event(organiser_id=1, url="https://example.org/event/swim3")
    registration.apply_fields(session, event, {
        "name": "Swim Sessions", "sport": "swimming", "distances": [],
        "occurrence": "specific_dates",
        "occurrences": [{"date_text": "18th Aug 2026", "date_iso": "2026-08-18"}],
    }, "bot")

    registration.apply_fields(session, event, {
        "name": "Swim Sessions", "sport": "swimming", "distances": [],
        "occurrence": "specific_dates",
        "occurrences": [{"date_text": "25th Aug 2026", "date_iso": "2026-08-25"}],
    }, "bot")

    assert len(event.occurrences) == 1
    assert event.occurrences[0].date_text == "25th Aug 2026"


# ---------------------------------------------------------------------------
# registration_status/registration_text/registration_opens_at/registration_closes_at -
# see common/models's RegistrationStatus docstring: whether an event needs sign-up/
# entry at all (parkrun doesn't), and if so, whether it's currently open - confirmed
# in practice on zigzagrunning.co.uk's Two Hundred Miles Challenge, which states
# outright "Registration is Closed" with no opening/closing date given at all.
# ---------------------------------------------------------------------------

def test_registration_closed_with_no_dates_stated(session):
    event = Event(organiser_id=1, url="https://example.org/event/200-miles")
    registration.apply_fields(session, event, {
        "name": "Two Hundred Miles Challenge", "sport": "running", "distances": [],
        "registration_status": "closed", "registration_text": "Registration is Closed",
    }, "bot")

    assert event.registration_status == RegistrationStatus.CLOSED
    assert event.registration_text == "Registration is Closed"
    assert event.registration_opens_at is None
    assert event.registration_closes_at is None


def test_registration_open_with_dates_and_times_parsed_into_combined_datetime(session):
    event = Event(organiser_id=1, url="https://example.org/event/some-10k")
    registration.apply_fields(session, event, {
        "name": "Some 10k", "sport": "running", "distances": [],
        "registration_status": "open", "registration_text": "Entries open 1 March, close 30 June 2026 23:59",
        "registration_opens_date_iso": "2026-03-01", "registration_opens_time_24h": "09:00",
        "registration_closes_date_iso": "2026-06-30", "registration_closes_time_24h": "23:59",
    }, "bot")

    assert event.registration_status == RegistrationStatus.OPEN
    assert event.registration_opens_at == datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    assert event.registration_closes_at == datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc)


def test_registration_date_without_time_defaults_to_midnight(session):
    event = Event(organiser_id=1, url="https://example.org/event/some-10k")
    registration.apply_fields(session, event, {
        "name": "Some 10k", "sport": "running", "distances": [],
        "registration_status": "open", "registration_opens_date_iso": "2026-03-01",
    }, "bot")

    assert event.registration_opens_at == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    assert event.registration_closes_at is None


def test_not_required_registration(session):
    event = Event(organiser_id=1, url="https://example.org/event/parkrun")
    registration.apply_fields(session, event, {
        "name": "Village parkrun", "sport": "running", "distances": [],
        "registration_status": "not_required",
    }, "bot")

    assert event.registration_status == RegistrationStatus.NOT_REQUIRED
    assert event.registration_text is None
    assert event.registration_opens_at is None
    assert event.registration_closes_at is None


def test_defaults_to_unknown_when_extraction_omits_registration_status(session):
    event = Event(organiser_id=1, url="https://example.org/event/plain")
    registration.apply_fields(session, event, {"name": "Plain Event", "sport": "running", "distances": []}, "bot")

    assert event.registration_status == RegistrationStatus.UNKNOWN
    assert event.registration_opens_at is None
    assert event.registration_closes_at is None


# ---------------------------------------------------------------------------
# lifecycle_status/lifecycle_text - see common/models's EventLifecycle docstring:
# whether the event itself is still going ahead, deliberately independent of
# registration_status (an event can be sold out and still on, or cancelled after
# entries were already closed).
# ---------------------------------------------------------------------------

def test_cancelled_event_lifecycle_fields_populated(session):
    event = Event(organiser_id=1, url="https://example.org/event/storm-10k")
    registration.apply_fields(session, event, {
        "name": "Storm-hit 10k", "sport": "running", "distances": [],
        "lifecycle_status": "cancelled", "lifecycle_text": "Cancelled due to adverse weather",
    }, "bot")

    assert event.lifecycle_status == EventLifecycle.CANCELLED
    assert event.lifecycle_text == "Cancelled due to adverse weather"


def test_postponed_event_lifecycle_fields_populated(session):
    event = Event(organiser_id=1, url="https://example.org/event/some-10k")
    registration.apply_fields(session, event, {
        "name": "Some 10k", "sport": "running", "distances": [],
        "lifecycle_status": "postponed", "lifecycle_text": "Postponed to 12 September 2026",
    }, "bot")

    assert event.lifecycle_status == EventLifecycle.POSTPONED
    assert event.lifecycle_text == "Postponed to 12 September 2026"


def test_defaults_to_scheduled_when_extraction_omits_lifecycle_status(session):
    event = Event(organiser_id=1, url="https://example.org/event/plain")
    registration.apply_fields(session, event, {"name": "Plain Event", "sport": "running", "distances": []}, "bot")

    assert event.lifecycle_status == EventLifecycle.SCHEDULED
    assert event.lifecycle_text is None


# ---------------------------------------------------------------------------
# Geocoding - see geocoding_client.py. Called once per crawl, result cached
# on the row, priority order matches admin/export's html_export._render_map.
# ---------------------------------------------------------------------------

def test_geocoding_populates_latitude_and_longitude(monkeypatch, session):
    captured = {}

    def fake_geocode(location, start_location, finish_location):
        captured["args"] = (location, start_location, finish_location)
        return (50.7, -1.98)

    monkeypatch.setattr(registration, "geocode_event_location", fake_geocode)

    event = Event(organiser_id=1, url="https://example.org/event/geo")
    registration.apply_fields(session, event, {
        "name": "Real Event", "sport": "running", "distances": [], "location": "Baiter Park, Poole",
    }, "bot")

    assert captured["args"] == ("Baiter Park, Poole", None, None)
    assert event.latitude == 50.7
    assert event.longitude == -1.98


def test_geocoding_failure_leaves_lat_lon_unset(session):
    event = Event(organiser_id=1, url="https://example.org/event/geo2")
    registration.apply_fields(session, event, {"name": "Real Event", "sport": "running", "distances": []}, "bot")

    assert event.latitude is None
    assert event.longitude is None


def test_known_latitude_and_longitude_used_directly_skipping_geocode_call(monkeypatch, session):
    monkeypatch.setattr(
        registration, "geocode_event_location",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must use the feed's own coordinates, not geocode")),
    )

    event = Event(organiser_id=1, url="https://www.parkrun.org.uk/bushy/")
    registration.apply_fields(session, event, {
        "name": "Bushy parkrun", "sport": "running", "distances": [],
        "latitude": 51.410992, "longitude": -0.335791,
    }, "bot")

    assert event.latitude == 51.410992
    assert event.longitude == -0.335791


# ---------------------------------------------------------------------------
# register_event_from_fields - the direct-from-source-data path (no robots check, no
# scrape, no LLM call at all - it isn't wired to any of those, unlike
# pattern_site/event_crawler.crawl_event), used by the structured-bulk-feed importers
# in feeds/feed_importers.py's registry (e.g. feeds/parkrun_import.py's run_import).
# See parkrun_import.build_event_fields for what a real fields dict looks like; this
# one is trimmed to just what matters per assertion.
# ---------------------------------------------------------------------------

_PARKRUN_FIELDS = {
    "name": "Bushy parkrun", "summary": "Bushy parkrun", "summary_alt": "Bushy parkrun",
    "summary_short": "Bushy parkrun", "sport": "running", "date_text": "Every Saturday, 9:00am",
    "location": "Bushy Park, Teddington", "start_location": "Bushy Park, Teddington",
    "finish_location": "Bushy Park, Teddington", "age_restriction_text": None,
    "is_valid_event": True, "invalid_reason": None, "registration_status": "not_required",
    "registration_text": None, "registration_opens_date_iso": None, "registration_opens_time_24h": None,
    "registration_closes_date_iso": None, "registration_closes_time_24h": None,
    "lifecycle_status": "scheduled", "lifecycle_text": None,
    "distances": [{"distance_text": "5k", "price_text": "Free", "distance_category": "5k"}],
    "occurrence": "weekly", "occurrence_weekdays": ["sat"], "occurrence_time": "09:00",
    "occurrence_starts_on": "2026-08-18", "occurrence_ends_on": None, "occurrences": [],
    "latitude": 51.410992, "longitude": -0.335791,
}


def test_register_event_from_fields_creates_event(session):
    event = registration.register_event_from_fields(
        session, organiser_id=1, event_url="https://www.parkrun.org.uk/bushy/",
        fields=_PARKRUN_FIELDS, registrator="jane_doe",
    )

    assert event is not None
    assert event.name == "Bushy parkrun"
    assert event.summary_alt == "Bushy parkrun"
    assert event.summary_short == "Bushy parkrun"
    assert event.registrator == "jane_doe"
    assert event.status == EventStatus.VALID
    assert event.registration_status == RegistrationStatus.NOT_REQUIRED
    assert event.occurrence == Occurrence.WEEKLY
    assert event.occurrence_weekdays == ["sat"]
    assert event.occurrence_starts_on == date(2026, 8, 18)
    assert event.occurrence_ends_on is None
    # The feed's own exact coordinates are used directly - not re-geocoded.
    assert event.latitude == 51.410992
    assert event.longitude == -0.335791
    assert len(event.distances) == 1
    assert event.distances[0].distance_text == "5k"
    assert event.distances[0].price_text == "Free"
    assert event.distances[0].registrator == "jane_doe"

    run = session.query(CrawlRun).one()
    assert run.status.value == "success"
    assert "registered directly from feed data" in run.detail


def test_register_event_from_fields_updates_existing_row_not_a_duplicate(session):
    registration.register_event_from_fields(
        session, organiser_id=1, event_url="https://www.parkrun.org.uk/bushy/",
        fields=_PARKRUN_FIELDS, registrator="bot",
    )
    updated_fields = {**_PARKRUN_FIELDS, "name": "Bushy parkrun (updated)"}

    event = registration.register_event_from_fields(
        session, organiser_id=1, event_url="https://www.parkrun.org.uk/bushy/",
        fields=updated_fields, registrator="jane_doe",
    )

    assert session.query(Event).count() == 1
    assert event.name == "Bushy parkrun (updated)"
    assert event.registrator == "jane_doe"
