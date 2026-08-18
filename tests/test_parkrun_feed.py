"""
Unit tests for parkrun_feed.py - no real network calls, requests.get is
monkeypatched with a canned events.json-shaped response, same seam-
monkeypatching style as test_sitemap_crawler.py's own requests.get tests.
"""

from datetime import date

import pytest

from services import parkrun_feed


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


_SAMPLE_FEED = {
    "countries": {
        "97": {"url": "www.parkrun.org.uk"},
        "1": {"url": "www.parkrun.us"},
    },
    "events": {
        "features": [
            {"properties": {"eventname": "bushy", "countrycode": 97}},
            {"properties": {"eventname": "southwark", "countrycode": 97}},
            {"properties": {"eventname": "central", "countrycode": 1}},  # different country - excluded
        ]
    },
}


@pytest.fixture(autouse=True)
def _allow_robots(monkeypatch):
    monkeypatch.setattr(parkrun_feed.robots, "is_allowed", lambda url, registrator="bot": True)


def test_builds_urls_for_the_requested_country_only(monkeypatch):
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_FEED))

    urls = parkrun_feed.get_event_urls(country_code=97)

    assert urls == ["https://www.parkrun.org.uk/bushy/", "https://www.parkrun.org.uk/southwark/"]


def test_different_country_code_gets_its_own_events(monkeypatch):
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_FEED))

    urls = parkrun_feed.get_event_urls(country_code=1)

    assert urls == ["https://www.parkrun.us/central/"]


def test_registrator_forwarded_to_robots_is_allowed(monkeypatch):
    captured = {}

    def fake_is_allowed(url, registrator="bot"):
        captured["registrator"] = registrator
        return True

    monkeypatch.setattr(parkrun_feed.robots, "is_allowed", fake_is_allowed)
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_FEED))

    parkrun_feed.get_event_urls(country_code=97, registrator="jane_doe")

    assert captured["registrator"] == "jane_doe"


def test_returns_none_when_robots_disallows(monkeypatch, capsys):
    monkeypatch.setattr(parkrun_feed.robots, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        parkrun_feed.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not fetch when robots disallows")),
    )

    assert parkrun_feed.get_event_urls() is None
    assert "ROBOTS-SKIP" in capsys.readouterr().out


def test_returns_none_on_fetch_failure(monkeypatch):
    def failing_get(*a, **kw):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(parkrun_feed.requests, "get", failing_get)

    assert parkrun_feed.get_event_urls() is None


def test_returns_none_when_country_code_unknown(monkeypatch):
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_FEED))

    assert parkrun_feed.get_event_urls(country_code=999) is None


def test_returns_none_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse({"unexpected": "shape"}))

    assert parkrun_feed.get_event_urls() is None


def test_features_missing_countrycode_or_eventname_are_skipped(monkeypatch):
    feed = {
        "countries": {"97": {"url": "www.parkrun.org.uk"}},
        "events": {"features": [
            {"properties": {"countrycode": 97}},  # no eventname
            {"properties": {"eventname": "bushy"}},  # no countrycode
            "not even a dict",
            {"properties": {"eventname": "southwark", "countrycode": 97}},
        ]},
    }
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(feed))

    urls = parkrun_feed.get_event_urls(country_code=97)

    assert urls == ["https://www.parkrun.org.uk/southwark/"]


def test_country_url_without_scheme_gets_https_prefix(monkeypatch):
    feed = {
        "countries": {"97": {"url": "www.parkrun.org.uk"}},
        "events": {"features": [{"properties": {"eventname": "bushy", "countrycode": 97}}]},
    }
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(feed))

    urls = parkrun_feed.get_event_urls(country_code=97)

    assert urls == ["https://www.parkrun.org.uk/bushy/"]


# ---------------------------------------------------------------------------
# build_event_fields - the direct-from-feed-data field mapping used when a
# registrator override is active (see listing_crawler.py's _parkrun_handler and
# event_crawler.register_event_from_fields). Pure function, no network at all.
# ---------------------------------------------------------------------------

_BUSHY_FEATURE = {
    "geometry": {"type": "Point", "coordinates": [-0.335791, 51.410992]},
    "properties": {
        "eventname": "bushy", "EventLongName": "Bushy parkrun", "EventShortName": "Bushy Park",
        "countrycode": 97, "seriesid": 1, "EventLocation": "Bushy Park, Teddington",
    },
}

_LLOYD_JUNIORS_FEATURE = {
    "geometry": {"type": "Point", "coordinates": [-0.06, 51.36]},
    "properties": {
        "eventname": "lloyd-juniors", "EventLongName": "Lloyd junior parkrun", "EventShortName": "Lloyd juniors",
        "countrycode": 97, "seriesid": 2, "EventLocation": "Lloyd Park",
    },
}


def test_build_event_fields_regular_event():
    fields = parkrun_feed.build_event_fields(_BUSHY_FEATURE, today=date(2026, 8, 18))

    assert fields["name"] == "Bushy parkrun"
    assert fields["summary"] == fields["summary_alt"] == fields["summary_short"] == "Bushy parkrun"
    assert fields["sport"] == "running"
    assert fields["location"] == fields["start_location"] == fields["finish_location"] == "Bushy Park, Teddington"
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
    # GeoJSON order is [longitude, latitude] - must not come out the wrong way round.
    assert fields["latitude"] == 51.410992
    assert fields["longitude"] == -0.335791


def test_build_event_fields_junior_event():
    fields = parkrun_feed.build_event_fields(_LLOYD_JUNIORS_FEATURE, today=date(2026, 8, 18))

    assert fields["name"] == "Lloyd junior parkrun"
    assert fields["age_restriction_text"] == "Ages 4-14"
    assert fields["distances"] == [{"distance_text": "2k", "price_text": "Free", "distance_category": "2_k"}]
    assert fields["occurrence_weekdays"] == ["sun"]
    assert fields["date_text"] == "Every Sunday, 9:00am"


def test_build_event_fields_defaults_today_to_the_real_current_date():
    fields = parkrun_feed.build_event_fields(_BUSHY_FEATURE)
    assert fields["occurrence_starts_on"] == date.today().isoformat()


def test_build_event_fields_missing_eventname_returns_none():
    feature = {"geometry": {"coordinates": [0, 0]}, "properties": {"EventLongName": "No slug"}}
    assert parkrun_feed.build_event_fields(feature) is None


def test_build_event_fields_falls_back_to_eventname_when_no_long_name():
    feature = {
        "geometry": {"coordinates": [-0.335791, 51.410992]},
        "properties": {"eventname": "bushy", "countrycode": 97},
    }
    fields = parkrun_feed.build_event_fields(feature)
    assert fields["name"] == "bushy"


# ---------------------------------------------------------------------------
# get_events - like get_event_urls, but pairs each URL with its own
# build_event_fields dict. Same fetch/robots/country-filtering machinery
# (_country_features), so only what's specific to this function is re-tested here.
# ---------------------------------------------------------------------------

_SAMPLE_FEED_WITH_GEOMETRY = {
    "countries": {"97": {"url": "www.parkrun.org.uk"}},
    "events": {"features": [_BUSHY_FEATURE, _LLOYD_JUNIORS_FEATURE]},
}


def test_get_events_pairs_urls_with_built_fields(monkeypatch):
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(_SAMPLE_FEED_WITH_GEOMETRY))

    events = parkrun_feed.get_events(country_code=97, today=date(2026, 8, 18))

    assert events == [
        ("https://www.parkrun.org.uk/bushy/", parkrun_feed.build_event_fields(_BUSHY_FEATURE, today=date(2026, 8, 18))),
        (
            "https://www.parkrun.org.uk/lloyd-juniors/",
            parkrun_feed.build_event_fields(_LLOYD_JUNIORS_FEATURE, today=date(2026, 8, 18)),
        ),
    ]


def test_get_events_returns_none_when_robots_disallows(monkeypatch, capsys):
    monkeypatch.setattr(parkrun_feed.robots, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        parkrun_feed.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not fetch when robots disallows")),
    )

    assert parkrun_feed.get_events() is None
    assert "ROBOTS-SKIP" in capsys.readouterr().out


def test_get_events_returns_none_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(
        parkrun_feed.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("no route to host")),
    )

    assert parkrun_feed.get_events() is None


def test_get_events_returns_empty_list_when_country_has_no_events(monkeypatch):
    feed = {
        "countries": {"97": {"url": "www.parkrun.org.uk"}},
        "events": {"features": [{"properties": {"eventname": "central", "countrycode": 1}}]},
    }
    monkeypatch.setattr(parkrun_feed.requests, "get", lambda *a, **kw: _FakeResponse(feed))

    # A real, usable country entry with zero matching features - genuinely different
    # from get_events() returning None (fetch/parse failure, or no country entry at all).
    assert parkrun_feed.get_events(country_code=97) == []
