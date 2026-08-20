"""
Unit tests for geocoding_client.py. No real network calls - requests.get is
monkeypatched with canned responses, same seam-monkeypatching style as
test_sitemap_crawler.py's own requests.get tests.
"""

import pytest

from services.events import geocoding_client


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch):
    # Every test gets a clean slate - otherwise whichever test happens to run
    # first "uses up" the throttle window and later tests would silently sleep.
    monkeypatch.setattr(geocoding_client, "_last_request_at", 0.0)
    monkeypatch.setattr(geocoding_client.time_module, "sleep", lambda seconds: None)


def test_geocode_returns_lat_lon_from_first_result(monkeypatch):
    monkeypatch.setattr(
        geocoding_client.requests, "get",
        lambda *a, **kw: _FakeResponse([{"lat": "50.838317", "lon": "-0.315052"}]),
    )

    result = geocoding_client.geocode("Lancing Manor Leisure Centre, Manor Road, Lancing")

    assert result == (50.838317, -0.315052)


def test_geocode_returns_none_for_blank_input(monkeypatch):
    monkeypatch.setattr(
        geocoding_client.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not call the API for blank input")),
    )

    assert geocoding_client.geocode(None) is None
    assert geocoding_client.geocode("") is None
    assert geocoding_client.geocode("   ") is None


def test_geocode_returns_none_when_no_results(monkeypatch):
    monkeypatch.setattr(geocoding_client.requests, "get", lambda *a, **kw: _FakeResponse([]))

    assert geocoding_client.geocode("Nowhere, Nonexistentshire") is None


def test_geocode_returns_none_on_request_failure(monkeypatch):
    def failing_get(*a, **kw):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(geocoding_client.requests, "get", failing_get)

    assert geocoding_client.geocode("Somewhere") is None


def test_geocode_returns_none_on_malformed_result(monkeypatch):
    monkeypatch.setattr(
        geocoding_client.requests, "get",
        lambda *a, **kw: _FakeResponse([{"lat": "not-a-number", "lon": "-0.31"}]),
    )

    assert geocoding_client.geocode("Somewhere") is None


def test_geocode_sends_descriptive_user_agent(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse([{"lat": "1.0", "lon": "2.0"}])

    monkeypatch.setattr(geocoding_client.requests, "get", fake_get)
    geocoding_client.geocode("Somewhere")

    assert "User-Agent" in captured["headers"]


def test_geocode_throttles_between_calls(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(geocoding_client.time_module, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(geocoding_client.requests, "get", lambda *a, **kw: _FakeResponse([{"lat": "1.0", "lon": "2.0"}]))

    geocoding_client.geocode("First place")
    geocoding_client.geocode("Second place")

    # The second call, hot on the heels of the first, must wait out Nominatim's
    # ~1 req/sec policy rather than firing immediately.
    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


# ---------------------------------------------------------------------------
# geocode_event_location - priority order matches export_events.py's own
# _render_map exactly (location, then start_location, then finish_location) -
# deliberately not a new/different priority.
# ---------------------------------------------------------------------------

def test_event_location_prefers_location_over_start_and_finish(monkeypatch):
    calls = []

    def fake_geocode(address):
        calls.append(address)
        return (1.0, 2.0)

    monkeypatch.setattr(geocoding_client, "geocode", fake_geocode)

    result = geocoding_client.geocode_event_location(
        location="Main Venue", start_location="Start Point", finish_location="Finish Point"
    )

    assert result == (1.0, 2.0)
    assert calls == ["Main Venue"]


def test_event_location_falls_back_to_start_location(monkeypatch):
    calls = []
    monkeypatch.setattr(geocoding_client, "geocode", lambda address: calls.append(address) or (1.0, 2.0))

    geocoding_client.geocode_event_location(location=None, start_location="Start Point", finish_location="Finish Point")

    assert calls == ["Start Point"]


def test_event_location_falls_back_to_finish_location(monkeypatch):
    calls = []
    monkeypatch.setattr(geocoding_client, "geocode", lambda address: calls.append(address) or (1.0, 2.0))

    geocoding_client.geocode_event_location(location=None, start_location=None, finish_location="Finish Point")

    assert calls == ["Finish Point"]


def test_event_location_returns_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(
        geocoding_client, "geocode",
        lambda address: (_ for _ in ()).throw(AssertionError("should not call geocode with nothing to geocode")),
    )

    assert geocoding_client.geocode_event_location(None, None, None) is None


def test_event_location_falls_through_to_next_field_when_first_fails(monkeypatch):
    # The bug this fixes: a non-blank location that Nominatim can't resolve must not
    # give up outright - start_location/finish_location are still worth trying.
    calls = []

    def fake_geocode(address):
        calls.append(address)
        return None if address == "Main Venue" else (1.0, 2.0)

    monkeypatch.setattr(geocoding_client, "geocode", fake_geocode)

    result = geocoding_client.geocode_event_location(
        location="Main Venue", start_location="Start Point", finish_location="Finish Point"
    )

    assert result == (1.0, 2.0)
    assert calls == ["Main Venue", "Start Point"]


# ---------------------------------------------------------------------------
# UK postcode fallback - the reported case: "Bakewell Showground, Bakewell. DE45 1AH"
# fails outright against Nominatim (confirmed against the real API - "Bakewell
# Showground" isn't indexed as a place at all), but "DE45 1AH" alone, extracted from
# that same string, resolves fine.
# ---------------------------------------------------------------------------

def test_event_location_retries_with_extracted_postcode_when_full_string_fails(monkeypatch):
    calls = []

    def fake_geocode(address):
        calls.append(address)
        return (53.2128596, -1.6693773) if address == "DE45 1AH" else None

    monkeypatch.setattr(geocoding_client, "geocode", fake_geocode)

    result = geocoding_client.geocode_event_location(
        location="Bakewell Showground, Bakewell. DE45 1AH", start_location=None, finish_location=None,
    )

    assert result == (53.2128596, -1.6693773)
    assert calls == ["Bakewell Showground, Bakewell. DE45 1AH", "DE45 1AH"]


def test_event_location_does_not_retry_postcode_when_candidate_is_already_bare_postcode(monkeypatch):
    calls = []
    monkeypatch.setattr(geocoding_client, "geocode", lambda address: calls.append(address) or None)

    geocoding_client.geocode_event_location(location="DE45 1AH", start_location=None, finish_location=None)

    # Extracting "DE45 1AH" from "DE45 1AH" would just be the same call again - skipped.
    assert calls == ["DE45 1AH"]


def test_event_location_falls_back_to_next_field_when_no_postcode_found_either(monkeypatch):
    calls = []
    monkeypatch.setattr(
        geocoding_client, "geocode",
        lambda address: calls.append(address) or (None if address == "Some Venue With No Postcode" else (1.0, 2.0)),
    )

    result = geocoding_client.geocode_event_location(
        location="Some Venue With No Postcode", start_location="Start Point", finish_location=None,
    )

    assert result == (1.0, 2.0)
    assert calls == ["Some Venue With No Postcode", "Start Point"]


def test_extract_uk_postcode_variants():
    assert geocoding_client._extract_uk_postcode("Bakewell Showground, Bakewell. DE45 1AH") == "DE45 1AH"
    assert geocoding_client._extract_uk_postcode("SW1A 1AA") == "SW1A 1AA"
    assert geocoding_client._extract_uk_postcode("No postcode in this string") is None
