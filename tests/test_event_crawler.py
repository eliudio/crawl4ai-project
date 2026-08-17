"""
Regression test for the run-frimley-2022 incident: a DB error while building/
storing an Event (there, a StringDataRightTruncation from an over-length
age_restriction_text - since fixed by widening that column to Text) must not
leave the session in a broken state for whatever runs next on it.

Before the fix, crawl_event's blanket `except Exception` recorded the failure
but never rolled back the session, so the *next* statement on that session
(here, simulated by session_scope's own closing commit) raised
PendingRollbackError instead of the original error - which crashed the whole
overnight local_runner batch instead of just costing it this one event.

Runs against a throwaway in-memory SQLite database, same spirit as
test_race_types.py - only the tables crawl_event actually touches are
created.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services import event_crawler
from services.models import CrawlRun, Event, EventDistance, RaceType


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    for table in (Event.__table__, CrawlRun.__table__, EventDistance.__table__, RaceType.__table__):
        table.metadata.create_all(engine, tables=[table])
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch):
    """Everything up to the point of failure: allowed by robots, dead-link
    preflight inconclusive (so it falls through instead of hitting the real
    network), scrapes fine, no JSON-LD, LLM returns fields - so the test's own
    injected failure (see test below) is the only thing that goes wrong."""
    monkeypatch.setattr(event_crawler.robots, "is_allowed", lambda url: True)
    monkeypatch.setattr(
        event_crawler.requests,
        "head",
        lambda url, **kw: (_ for _ in ()).throw(event_crawler.requests.exceptions.ConnectionError()),
    )
    monkeypatch.setattr(
        event_crawler.scraper_client,
        "scrape",
        lambda url, want_links=False, want_html=False: ("markdown", [], "<html></html>", url),
    )
    monkeypatch.setattr(event_crawler.structured_data, "extract_event_fields", lambda html: {})


def test_exception_during_build_rolls_back_session(monkeypatch, session):
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: {
            "name": "Run Frimley 2022",
            "sport": "running",
            "distances": [{"distance_text": "10k", "distance_category": "10k"}],
        },
    )

    # Stands in for the real incident's failure mode: a genuine flush-level DB
    # error (there, Postgres's StringDataRightTruncation on autoflush; here, a
    # NOT NULL violation via an explicit flush) rather than a plain Python
    # exception - that's what actually invalidates the session's transaction
    # and requires rollback() before anything else can run on it.
    def _boom(session, sport, distance_category):
        session.add(Event(organiser_id=1, url=None, name="bad"))
        session.flush()

    monkeypatch.setattr(event_crawler, "get_or_create_race_type", _boom)

    result = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/x")

    assert result is None

    # The real regression check: the session must still be usable afterwards -
    # session_scope's own closing commit() is exactly this kind of "next
    # statement" and must not raise PendingRollbackError.
    session.commit()

    # No half-built Event row should have survived the rollback.
    assert session.query(Event).count() == 0
    # But the failure is still recorded for visibility.
    run = session.query(CrawlRun).one()
    assert "IntegrityError" in run.detail


# ---------------------------------------------------------------------------
# Dead-link preflight: the raceforlife.cancerresearchuk.org incident - a
# genuinely 404ing event URL that crawl4ai still "successfully" rendered as a
# near-empty page, so extract_event_fields kept returning None, no Event row
# was ever stored, and listing_crawler kept reporting the URL as "new" and
# retrying it every single run forever.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_confirmed_dead_link_marks_event_invalid_without_scraping(monkeypatch, session):
    monkeypatch.setattr(event_crawler.requests, "head", lambda url, **kw: _FakeResponse(404))
    scrape_calls = []
    monkeypatch.setattr(
        event_crawler.scraper_client, "scrape", lambda *a, **kw: scrape_calls.append((a, kw))
    )

    event = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/gone")

    assert scrape_calls == []
    assert event is not None
    assert event.status == event_crawler.EventStatus.INVALID
    assert "404" in event.invalid_reason
    session.commit()  # must not corrupt the session for whatever runs next

    # The URL is now a real Event row - the next listing crawl's "already in the
    # database" check (see listing_crawler.py) will exclude it from "new" from now on.
    assert session.query(Event).filter_by(url="https://example.org/event/gone").count() == 1


def test_confirmed_dead_link_updates_existing_row_not_a_duplicate(monkeypatch, session):
    existing = Event(organiser_id=1, url="https://example.org/event/gone", name="Old Name")
    session.add(existing)
    session.commit()

    monkeypatch.setattr(event_crawler.requests, "head", lambda url, **kw: _FakeResponse(410))

    event = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/gone")

    assert event.id == existing.id
    assert session.query(Event).count() == 1
    assert event.status == event_crawler.EventStatus.INVALID


def test_inconclusive_preflight_falls_through_to_normal_scrape(monkeypatch, session):
    # A blocked/timed-out direct request isn't evidence the page is dead - must
    # still attempt the real scrape rather than mark it invalid on a guess.
    monkeypatch.setattr(
        event_crawler.requests,
        "head",
        lambda url, **kw: (_ for _ in ()).throw(event_crawler.requests.exceptions.ConnectTimeout()),
    )
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: {"name": "Real Event", "sport": "running", "distances": []},
    )

    event = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/real")

    assert event.status == event_crawler.EventStatus.VALID
    assert event.name == "Real Event"


def test_non_dead_status_code_falls_through_to_normal_scrape(monkeypatch, session):
    # A 200 (or any code outside _DEAD_LINK_STATUS_CODES) is not a dead link either.
    monkeypatch.setattr(event_crawler.requests, "head", lambda url, **kw: _FakeResponse(200))
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: {"name": "Real Event", "sport": "running", "distances": []},
    )

    event = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/real")

    assert event.status == event_crawler.EventStatus.VALID


# ---------------------------------------------------------------------------
# check_mode="force" - re-crawl and always re-extract, even when the content
# hasn't changed, replacing the existing row in place. See local_runner.py's
# --force-refresh: the reported case is re-running the Three Forts Challenge
# organiser after fixing a bug in extraction, where every already-crawled
# event's content_hash still matches (the live page didn't change, the code
# that reads it did) - hash-check's normal "unchanged, skip" shortcut would
# otherwise never let the fix take effect until the page itself changes.
# ---------------------------------------------------------------------------

def test_unknown_check_mode_raises(session):
    with pytest.raises(ValueError, match="unknown check_mode"):
        event_crawler.crawl_event(
            session, organiser_id=1, event_url="https://example.org/event/x", check_mode="bogus"
        )


def test_hash_check_skips_reextraction_when_content_unchanged(monkeypatch, session):
    # Baseline this is contrasted with below: hash-check's whole point is to skip
    # the LLM call when the page's content hasn't changed since last time.
    existing = Event(
        organiser_id=1, url="https://example.org/event/x", name="Old Name",
        content_hash=event_crawler._hash("markdown"),
    )
    session.add(existing)
    session.commit()

    extract_calls = []
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: extract_calls.append(1) or {"name": "New Name", "distances": []},
    )

    event = event_crawler.crawl_event(
        session, organiser_id=1, event_url="https://example.org/event/x", check_mode="hash-check"
    )

    assert extract_calls == []
    assert event.name == "Old Name"


def test_force_reextracts_and_replaces_even_when_hash_unchanged(monkeypatch, session):
    # Same matching content_hash as the hash-check test above - force must not skip.
    existing = Event(
        organiser_id=1, url="https://example.org/event/x", name="Old Name",
        content_hash=event_crawler._hash("markdown"),
    )
    session.add(existing)
    session.commit()

    extract_calls = []
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: extract_calls.append(1) or {"name": "New Name", "distances": []},
    )

    event = event_crawler.crawl_event(
        session, organiser_id=1, event_url="https://example.org/event/x", check_mode="force"
    )

    assert len(extract_calls) == 1
    assert event.id == existing.id  # replaced in place, not a duplicate row
    assert event.name == "New Name"
    assert session.query(Event).count() == 1


def test_force_adds_new_event_when_none_exists(monkeypatch, session):
    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: {"name": "Brand New", "distances": []},
    )

    event = event_crawler.crawl_event(
        session, organiser_id=1, event_url="https://example.org/event/new", check_mode="force"
    )

    assert event is not None
    assert event.name == "Brand New"
    assert session.query(Event).count() == 1


def test_force_ignores_url_check_style_skip(monkeypatch, session):
    # force must fetch and re-extract even though the URL already exists - the
    # thing url-check would normally skip on outright.
    existing = Event(organiser_id=1, url="https://example.org/event/x", name="Old Name")
    session.add(existing)
    session.commit()

    monkeypatch.setattr(
        event_crawler.llm_extractor,
        "extract_event_fields",
        lambda url, markdown, known_fields=None: {"name": "New Name", "distances": []},
    )

    event = event_crawler.crawl_event(
        session, organiser_id=1, event_url="https://example.org/event/x", check_mode="force"
    )

    assert event.name == "New Name"


# ---------------------------------------------------------------------------
# robots.txt skip must be clearly logged - crawl_event returning None here
# looks identical, from local_runner.py's/main.py's own "ok"/"FAILED" print,
# to a genuine scrape/extraction failure. The ROBOTS-SKIP marker is what makes
# the two distinguishable in the log (see also listing_crawler.py/
# sitemap_crawler.py's own skip sites, which use the same marker).
# ---------------------------------------------------------------------------

def test_robots_disallowed_event_is_logged_clearly(monkeypatch, session, capsys):
    monkeypatch.setattr(event_crawler.robots, "is_allowed", lambda url: False)
    monkeypatch.setattr(
        event_crawler.scraper_client, "scrape",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not scrape a robots-disallowed event")),
    )

    result = event_crawler.crawl_event(session, organiser_id=1, event_url="https://example.org/event/blocked")

    assert result is None
    assert "ROBOTS-SKIP: https://example.org/event/blocked (event)" in capsys.readouterr().out

    run = session.query(CrawlRun).one()
    assert run.status == event_crawler.CrawlStatus.SKIPPED
    assert run.detail == "disallowed by robots.txt"
