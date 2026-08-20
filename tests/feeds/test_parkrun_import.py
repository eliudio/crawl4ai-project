"""
Unit tests for parkrun_import.py - no real network calls, requests.get is
monkeypatched with a canned events-table.tsv-shaped response, same seam-
monkeypatching style test_sitemap_crawler.py/the old test_parkrun_feed.py used.

robots.is_allowed() is permissive by default across the whole suite (see
tests/conftest.py's autouse _stub_robots_network) - unlike the old parkrun_feed.py,
there's no extra "refuse regardless of robots.is_allowed" gate here to test
separately: see this module's own docstring for why a plain "bot" fetch of this
particular (openly licensed, third-party-hosted) TSV doesn't need one.
"""

from datetime import date

import pytest

from services.common.models import Organiser, SourceType
from services.feeds import feed_importers, parkrun_import

_SAMPLE_TSV = (
    "Event\tLatitude\tLongitude\tCountry\tState\tCounty\tStatus\tCancellations\tWebsite\n"
    "Bushy parkrun\t51.410992\t-0.335791\tUnited Kingdom\tEngland\tGreater London\tparkrunning\t[]\thttps://www.parkrun.org.uk/bushy\n"
    "Lloyd junior parkrun\t51.36\t-0.06\tUnited Kingdom\tEngland\t-Unknown-\tjunior parkrunning\t[]\thttps://www.parkrun.org.uk/lloyd-juniors\n"
    "Aachener Weiher parkrun\t50.934298\t6.927553\tGermany\tNorth Rhine-Westphalia\tCologne District\tparkrunning\t[]\thttps://www.parkrun.com.de/aachenerweiher\n"
)


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# fetch_rows / get_events - the fetch+parse+country-filter path.
# ---------------------------------------------------------------------------

def test_fetch_rows_parses_tsv_into_dicts(monkeypatch):
    monkeypatch.setattr(parkrun_import.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_TSV))

    rows = parkrun_import.fetch_rows()

    assert len(rows) == 3
    assert rows[0]["Event"] == "Bushy parkrun"
    assert rows[0]["Website"] == "https://www.parkrun.org.uk/bushy"


def test_fetch_rows_returns_none_when_robots_disallows(monkeypatch, capsys):
    monkeypatch.setattr(parkrun_import, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        parkrun_import.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not fetch when robots disallows")),
    )

    assert parkrun_import.fetch_rows() is None
    assert "ROBOTS-SKIP" in capsys.readouterr().out


def test_fetch_rows_returns_none_on_fetch_failure(monkeypatch):
    def failing_get(*a, **kw):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(parkrun_import.requests, "get", failing_get)

    assert parkrun_import.fetch_rows() is None


def test_get_events_filters_to_the_requested_country_only(monkeypatch):
    monkeypatch.setattr(parkrun_import.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_TSV))

    events = parkrun_import.get_events(country="United Kingdom", today=date(2026, 8, 18))

    urls = [url for url, _fields in events]
    assert urls == ["https://www.parkrun.org.uk/bushy/", "https://www.parkrun.org.uk/lloyd-juniors/"]


def test_get_events_different_country_gets_its_own_rows(monkeypatch):
    monkeypatch.setattr(parkrun_import.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_TSV))

    events = parkrun_import.get_events(country="Germany", today=date(2026, 8, 18))

    urls = [url for url, _fields in events]
    assert urls == ["https://www.parkrun.com.de/aachenerweiher/"]


def test_get_events_returns_none_when_feed_unusable(monkeypatch):
    monkeypatch.setattr(
        parkrun_import.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("no route to host")),
    )

    assert parkrun_import.get_events() is None


# ---------------------------------------------------------------------------
# build_event_fields - the direct-from-row field mapping. Pure function, no network.
# ---------------------------------------------------------------------------

_BUSHY_ROW = {
    "Event": "Bushy parkrun", "Latitude": "51.410992", "Longitude": "-0.335791",
    "Country": "United Kingdom", "State": "England", "County": "Greater London",
    "Status": "parkrunning", "Cancellations": "[]", "Website": "https://www.parkrun.org.uk/bushy",
}

_LLOYD_JUNIORS_ROW = {
    "Event": "Lloyd junior parkrun", "Latitude": "51.36", "Longitude": "-0.06",
    "Country": "United Kingdom", "State": "England", "County": "-Unknown-",
    "Status": "junior parkrunning", "Cancellations": "[]", "Website": "https://www.parkrun.org.uk/lloyd-juniors",
}


def test_build_event_fields_regular_event():
    fields = parkrun_import.build_event_fields(_BUSHY_ROW, today=date(2026, 8, 18))

    assert fields["name"] == "Bushy parkrun"
    assert fields["summary"] == fields["summary_alt"] == fields["summary_short"] == "Bushy parkrun"
    assert fields["sport"] == "running"
    assert fields["location"] == fields["start_location"] == fields["finish_location"] == "Greater London, England, United Kingdom"
    assert fields["age_restriction_text"] is None
    assert fields["is_valid_event"] is True
    assert fields["registration_status"] == "not_required"
    assert fields["lifecycle_status"] == "scheduled"
    assert fields["distances"] == [{"distance_text": "5k", "price_text": "Free", "distance_category": "5k"}]
    assert fields["occurrence"] == "weekly"
    assert fields["occurrence_weekdays"] == ["sat"]
    assert fields["occurrence_time"] == "09:00"
    assert fields["date_text"] == "Every Saturday, 9:00am"
    assert fields["occurrence_starts_on"] == "2026-08-18"
    assert fields["occurrence_ends_on"] is None
    assert fields["occurrences"] == []
    assert fields["latitude"] == 51.410992
    assert fields["longitude"] == -0.335791


def test_build_event_fields_junior_event_skips_unknown_county():
    fields = parkrun_import.build_event_fields(_LLOYD_JUNIORS_ROW, today=date(2026, 8, 18))

    assert fields["name"] == "Lloyd junior parkrun"
    assert fields["age_restriction_text"] == "Ages 4-14"
    assert fields["distances"] == [{"distance_text": "2k", "price_text": "Free", "distance_category": "2_k"}]
    assert fields["occurrence_weekdays"] == ["sun"]
    assert fields["date_text"] == "Every Sunday, 9:00am"
    # "-Unknown-" county dropped, not passed through literally.
    assert fields["location"] == "England, United Kingdom"


def test_build_event_fields_defaults_today_to_the_real_current_date():
    fields = parkrun_import.build_event_fields(_BUSHY_ROW)
    assert fields["occurrence_starts_on"] == date.today().isoformat()


def test_build_event_fields_missing_website_returns_none():
    row = {**_BUSHY_ROW, "Website": ""}
    assert parkrun_import.build_event_fields(row) is None


def test_build_event_fields_missing_event_name_returns_none():
    row = {**_BUSHY_ROW, "Event": ""}
    assert parkrun_import.build_event_fields(row) is None


def test_build_event_fields_malformed_coordinates_are_null_not_a_crash():
    row = {**_BUSHY_ROW, "Latitude": "not-a-number"}
    fields = parkrun_import.build_event_fields(row)
    assert fields["latitude"] is None
    assert fields["longitude"] is None


# ---------------------------------------------------------------------------
# feed_importers.get_or_create_organiser - shared bootstrap helper.
# ---------------------------------------------------------------------------

class _FakeOrganiserSession:
    def __init__(self, existing: Organiser | None = None):
        self._existing = existing
        self.added: list[Organiser] = []
        self.flushed = False

    def scalar(self, query):
        return self._existing

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 99


def test_get_or_create_organiser_creates_when_missing():
    session = _FakeOrganiserSession(existing=None)

    organiser = feed_importers.get_or_create_organiser(
        session, name="parkrun UK", homepage_url="https://www.parkrun.org.uk/", discovered_via="feed_import:parkrun",
    )

    assert organiser.name == "parkrun UK"
    assert organiser.source_type == SourceType.PLATFORM
    assert organiser.registrator == "bot"
    assert organiser.discovered_via == "feed_import:parkrun"
    assert organiser.id == 99  # assigned by the fake session's flush()
    assert session.flushed


def test_get_or_create_organiser_reuses_existing_row_by_name():
    existing = Organiser(name="parkrun UK", homepage_url="https://www.parkrun.org.uk/", source_type=SourceType.PLATFORM)
    session = _FakeOrganiserSession(existing=existing)

    organiser = feed_importers.get_or_create_organiser(
        session, name="parkrun UK", homepage_url="https://www.parkrun.org.uk/", discovered_via="feed_import:parkrun",
    )

    assert organiser is existing
    assert session.added == []  # nothing to sync - already PLATFORM


def test_get_or_create_organiser_heals_source_type_on_a_pre_existing_row():
    # An organiser row seeded before this importer existed (the old
    # organisers_seed.csv "parkrun UK" row, source_type=ORGANISER) must be pulled out
    # of the pattern-website pipeline's own eligibility check on the very next run,
    # not left as source_type=ORGANISER forever.
    existing = Organiser(name="parkrun UK", homepage_url="https://www.parkrun.org.uk/", source_type=SourceType.ORGANISER)
    session = _FakeOrganiserSession(existing=existing)

    organiser = feed_importers.get_or_create_organiser(
        session, name="parkrun UK", homepage_url="https://www.parkrun.org.uk/", discovered_via="feed_import:parkrun",
    )

    assert organiser is existing
    assert organiser.source_type == SourceType.PLATFORM
    assert session.added == [existing]


# ---------------------------------------------------------------------------
# run_import - the registered "parkrun" importer's own dispatch: always "bot" (no
# registrator override branch, unlike the old _parkrun_handler this pipeline
# replaced - see this module's own docstring for why that's the right call here).
# get_or_create_organiser/get_events/register_event_from_fields are exercised on
# their own above/in test_event_crawler.py; this is only about run_import's wiring.
# ---------------------------------------------------------------------------

@pytest.fixture
def _stub_get_or_create_organiser(monkeypatch):
    # Not autouse - the get_or_create_organiser tests above exercise the real thing;
    # only run_import's own dispatch tests below need it stubbed out.
    organiser = Organiser(name="parkrun UK", homepage_url="https://www.parkrun.org.uk/")
    organiser.id = 7
    # parkrun_import.run_import calls its own bare-imported get_or_create_organiser,
    # not feed_importers.get_or_create_organiser (a separate reference bound at
    # import time) - that's the seam to patch.
    monkeypatch.setattr(parkrun_import, "get_or_create_organiser", lambda session, **kw: organiser)
    return organiser


def test_run_import_registers_every_event_as_bot(monkeypatch, _stub_get_or_create_organiser):
    captured = {"registered": []}

    def fake_get_events(country="United Kingdom", registrator="bot", today=None):
        captured["registrator"] = registrator
        captured["country"] = country
        return [
            ("https://www.parkrun.org.uk/bushy/", {"name": "Bushy parkrun"}),
            ("https://www.parkrun.org.uk/lloyd-juniors/", {"name": "Lloyd junior parkrun"}),
        ]

    def fake_register(session_arg, organiser_id, event_url, fields, registrator):
        captured["registered"].append((organiser_id, event_url, fields, registrator))

    monkeypatch.setattr(parkrun_import, "get_events", fake_get_events)
    monkeypatch.setattr(parkrun_import, "register_event_from_fields", fake_register)

    summary = parkrun_import.run_import(session=object(), params={})

    assert captured["registrator"] == "bot"  # hardcoded, never taken from params
    assert captured["country"] == "United Kingdom"
    assert captured["registered"] == [
        (7, "https://www.parkrun.org.uk/bushy/", {"name": "Bushy parkrun"}, "bot"),
        (7, "https://www.parkrun.org.uk/lloyd-juniors/", {"name": "Lloyd junior parkrun"}, "bot"),
    ]
    assert summary == {"status": "ok", "registered": 2, "organiser_id": 7}


def test_run_import_respects_country_param(monkeypatch, _stub_get_or_create_organiser):
    captured = {}

    def fake_get_events(country="United Kingdom", registrator="bot", today=None):
        captured["country"] = country
        return []

    monkeypatch.setattr(parkrun_import, "get_events", fake_get_events)

    parkrun_import.run_import(session=object(), params={"country": "Germany"})

    assert captured["country"] == "Germany"


def test_run_import_returns_unusable_summary_when_feed_is_none(monkeypatch, _stub_get_or_create_organiser):
    monkeypatch.setattr(parkrun_import, "get_events", lambda **kw: None)
    monkeypatch.setattr(
        parkrun_import, "register_event_from_fields",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not register anything when the feed is unusable")),
    )

    summary = parkrun_import.run_import(session=object(), params={})

    assert summary == {"status": "unusable", "registered": 0, "organiser_id": 7}


def test_parkrun_registered_under_its_own_name():
    assert feed_importers.get_importer("parkrun") is parkrun_import.run_import
