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
    """Everything up to the point of failure: allowed by robots, scrapes fine,
    no JSON-LD, LLM returns fields - so the test's own injected failure (see
    test below) is the only thing that goes wrong."""
    monkeypatch.setattr(event_crawler.robots, "is_allowed", lambda url: True)
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
