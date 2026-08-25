"""
Unit tests for admin/web/app.py - the thin FastAPI routing layer. queries.py
(the only thing that ever touches the database - see its own module
docstring) is monkeypatched at the module level app.py actually calls it
through, so no real database is ever touched; render.py runs for real, so
these also confirm the two are wired together correctly (right status
filter/organiser_id/search reaching queries.py, the right rows reaching the
right render.py function).
"""

from fastapi.testclient import TestClient

from services.admin.web import queries
from services.admin.web.app import app
from services.common.config import settings
from services.common.models import Event, EventStatus

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_no_auth_required_when_basic_auth_settings_unset(monkeypatch):
    monkeypatch.setattr(settings, "admin_basic_auth_user", None)
    monkeypatch.setattr(settings, "admin_basic_auth_password", None)
    monkeypatch.setattr(queries, "fetch_organisers", lambda session: [])

    response = client.get("/")

    assert response.status_code == 200


def test_rejects_missing_credentials_when_basic_auth_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_basic_auth_user", "maintainer")
    monkeypatch.setattr(settings, "admin_basic_auth_password", "s3cret")

    response = client.get("/")

    assert response.status_code == 401


def test_rejects_wrong_credentials_when_basic_auth_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_basic_auth_user", "maintainer")
    monkeypatch.setattr(settings, "admin_basic_auth_password", "s3cret")

    response = client.get("/", auth=("maintainer", "wrong"))

    assert response.status_code == 401


def test_accepts_correct_credentials_when_basic_auth_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_basic_auth_user", "maintainer")
    monkeypatch.setattr(settings, "admin_basic_auth_password", "s3cret")
    monkeypatch.setattr(queries, "fetch_organisers", lambda session: [])

    response = client.get("/", auth=("maintainer", "s3cret"))

    assert response.status_code == 200


def test_healthz_unaffected_by_basic_auth_configuration(monkeypatch):
    monkeypatch.setattr(settings, "admin_basic_auth_user", "maintainer")
    monkeypatch.setattr(settings, "admin_basic_auth_password", "s3cret")

    response = client.get("/healthz")

    assert response.status_code == 200


def test_index_lists_organisers(monkeypatch):
    monkeypatch.setattr(queries, "fetch_organisers", lambda session: [(1, "Acme Runners", 3)])

    response = client.get("/")

    assert response.status_code == 200
    assert "Acme Runners" in response.text
    assert 'href="/events?organiser_id=1"' in response.text


def test_events_requests_valid_status_and_passes_filters_through(monkeypatch):
    captured = {}

    def fake_fetch_events(session, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(queries, "fetch_events", fake_fetch_events)

    response = client.get("/events", params={"organiser_id": 7, "q": "marathon"})

    assert response.status_code == 200
    assert captured == {"organiser_id": 7, "status": EventStatus.VALID, "search": "marathon"}
    assert "Events per organiser" in response.text


def test_events_invalid_requests_invalid_status(monkeypatch):
    captured = {}

    def fake_fetch_events(session, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(queries, "fetch_events", fake_fetch_events)

    response = client.get("/events/invalid")

    assert response.status_code == 200
    assert captured["status"] == EventStatus.INVALID
    assert "Invalid events" in response.text


def test_events_by_type_requests_valid_status_and_renders_tree(monkeypatch):
    event = Event(id=1, organiser_id=1, name="Acme 5K", sport="running", date_text=None, distances=[], raw_markdown=None)
    captured = {}

    def fake_fetch_events(session, **kwargs):
        captured.update(kwargs)
        return [(event, "Acme Runners")]

    monkeypatch.setattr(queries, "fetch_events", fake_fetch_events)

    response = client.get("/events/by-type", params={"organiser_id": 3})

    assert response.status_code == 200
    assert captured["status"] == EventStatus.VALID
    assert captured["organiser_id"] == 3
    assert "search" not in captured  # by-type has no search filter, unlike /events
    assert "Events per race type" in response.text


def test_events_with_no_filters_passes_none(monkeypatch):
    captured = {}

    def fake_fetch_events(session, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(queries, "fetch_events", fake_fetch_events)

    client.get("/events")

    assert captured["organiser_id"] is None
    assert captured["search"] is None
