"""
Unit tests for parkrun_feed.py - no real network calls, requests.get is
monkeypatched with a canned events.json-shaped response, same seam-
monkeypatching style as test_sitemap_crawler.py's own requests.get tests.
"""

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
