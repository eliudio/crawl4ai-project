"""
Unit tests for local_event_scraper.run()'s --mode handling:
- "normal": crawl every new event URL found for each organiser.
- "dry-run": print discovered event URLs, never crawl/store them.
- "sanity-check": crawl only the first new event URL per organiser - a quick
  smoke test across every organiser, the opposite trade-off from --limit
  (fewer events per organiser instead of fewer organisers).

No real DB/network: init_db/seed_from_csv/session_scope and both pipeline
stages (listing_crawler.crawl_listing, event_crawler.crawl_event) are all
monkeypatched with canned/tracked stand-ins.
"""

from contextlib import contextmanager

import pytest

from services import local_event_scraper
from services.models import Event, Organiser


def _make_organiser(id_: int, name: str) -> Organiser:
    organiser = Organiser(name=name, homepage_url=f"https://{name.lower()}.example.com/")
    organiser.id = id_
    return organiser


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)


class _FakeSession:
    """Only .scalars()/.get() are ever called by run() - no real DB needed."""

    def __init__(self, organisers):
        self._organisers = {o.id: o for o in organisers}

    def scalars(self, query):
        return _FakeScalars(list(self._organisers.values()))

    def get(self, model, id_):
        return self._organisers[id_]


@pytest.fixture
def organisers():
    return [_make_organiser(1, "Acme"), _make_organiser(2, "Beta")]


@pytest.fixture(autouse=True)
def _stub_infra(monkeypatch, organisers):
    monkeypatch.setattr(local_event_scraper, "init_db", lambda: None)
    monkeypatch.setattr(local_event_scraper, "seed_from_csv", lambda: None)

    @contextmanager
    def fake_session_scope():
        yield _FakeSession(organisers)

    monkeypatch.setattr(local_event_scraper, "session_scope", fake_session_scope)


def _stub_new_urls(monkeypatch, urls_by_organiser: dict[int, list[str]]):
    monkeypatch.setattr(
        local_event_scraper.listing_crawler, "crawl_listing",
        lambda session, organiser, force=False, dry_run=False, event_limit=None: urls_by_organiser.get(organiser.id, []),
    )


class _Calls(list):
    """Plain list (so `calls == [...]` keeps working everywhere) plus a
    side-channel .check_modes list for tests that care which check_mode each
    crawl_event() call actually got."""

    check_modes: list[str]


def _stub_crawl_event(monkeypatch):
    calls = _Calls()
    calls.check_modes = []

    def fake_crawl_event(session, organiser_id, url, check_mode="hash-check"):
        calls.append((organiser_id, url))
        calls.check_modes.append(check_mode)
        return Event(organiser_id=organiser_id, url=url)

    monkeypatch.setattr(local_event_scraper.event_crawler, "crawl_event", fake_crawl_event)
    return calls


def test_normal_mode_crawls_every_new_url(monkeypatch):
    _stub_new_urls(monkeypatch, {
        1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"],
        2: ["https://beta.example.com/event/c"],
    })
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="normal")

    assert calls == [
        (1, "https://acme.example.com/event/a"),
        (1, "https://acme.example.com/event/b"),
        (2, "https://beta.example.com/event/c"),
    ]


def test_dry_run_mode_never_crawls_events(monkeypatch, capsys):
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"]})
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="dry-run")

    assert calls == []
    out = capsys.readouterr().out
    assert "[dry-run] https://acme.example.com/event/a" in out
    assert "[dry-run] https://acme.example.com/event/b" in out


def test_sanity_check_mode_crawls_only_first_url_per_organiser(monkeypatch):
    _stub_new_urls(monkeypatch, {
        1: [
            "https://acme.example.com/event/a",
            "https://acme.example.com/event/b",
            "https://acme.example.com/event/c",
        ],
        2: ["https://beta.example.com/event/d"],
    })
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="sanity-check")

    # Exactly one event per organiser, even though Acme had three new URLs.
    assert calls == [
        (1, "https://acme.example.com/event/a"),
        (2, "https://beta.example.com/event/d"),
    ]


def test_sanity_check_mode_handles_organiser_with_no_new_urls(monkeypatch):
    _stub_new_urls(monkeypatch, {1: [], 2: ["https://beta.example.com/event/d"]})
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="sanity-check")

    assert calls == [(2, "https://beta.example.com/event/d")]


def test_default_mode_is_normal(monkeypatch):
    """run() called with no mode argument at all behaves like mode="normal"."""
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a"]})
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run()

    assert calls == [(1, "https://acme.example.com/event/a")]


# ---------------------------------------------------------------------------
# mode -> dry_run/event_limit forwarded to crawl_listing itself, not just used to
# slice/skip whatever it returns - see crawl_listing's own docstring for why: a
# handler that writes real event data inline (there was one - the old "parkrun"
# handler's registrator-override path, see git history and feed_importers.py, the
# separate pipeline that replaced it) would have no other way to find out about
# --mode sanity-check/dry-run at all, since by the time it would return something for
# the code below to slice, it's too late. No currently-registered handler needs this,
# but the mechanism stays exercised here in case a future one does.
# ---------------------------------------------------------------------------

def _stub_crawl_listing_capture(monkeypatch):
    captured = {}

    def fake_crawl_listing(session, organiser, force=False, dry_run=False, event_limit=None):
        captured[organiser.id] = {"dry_run": dry_run, "event_limit": event_limit}
        return []

    monkeypatch.setattr(local_event_scraper.listing_crawler, "crawl_listing", fake_crawl_listing)
    return captured


def test_normal_mode_passes_no_dry_run_and_no_event_limit(monkeypatch):
    captured = _stub_crawl_listing_capture(monkeypatch)
    _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="normal", organiser_id=1)

    assert captured[1] == {"dry_run": False, "event_limit": None}


def test_sanity_check_mode_passes_event_limit_one(monkeypatch):
    captured = _stub_crawl_listing_capture(monkeypatch)
    _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="sanity-check", organiser_id=1)

    assert captured[1] == {"dry_run": False, "event_limit": 1}


def test_dry_run_mode_passes_dry_run_true(monkeypatch):
    captured = _stub_crawl_listing_capture(monkeypatch)
    _stub_crawl_event(monkeypatch)

    local_event_scraper.run(mode="dry-run", organiser_id=1)

    assert captured[1] == {"dry_run": True, "event_limit": None}


# ---------------------------------------------------------------------------
# --force-refresh: re-crawl every event URL for an organiser (not just new
# ones) and always re-extract - see the reported case, re-running the Three
# Forts Challenge organiser after fixing the distance-stripping bug.
# ---------------------------------------------------------------------------

def test_force_refresh_threads_force_through_to_crawl_listing(monkeypatch):
    # _FakeSession.scalars() (see above) doesn't actually filter by organiser_id -
    # it's the real query's job, not exercised here - so this checks force=True
    # reaches crawl_listing for whichever organisers run() does process.
    captured = {}

    def fake_crawl_listing(session, organiser, force=False, dry_run=False, event_limit=None):
        captured[organiser.id] = force
        return []

    monkeypatch.setattr(local_event_scraper.listing_crawler, "crawl_listing", fake_crawl_listing)
    _stub_crawl_event(monkeypatch)

    local_event_scraper.run(organiser_id=1, force_refresh=True)

    assert captured == {1: True, 2: True}


def test_normal_run_does_not_force_crawl_listing(monkeypatch):
    captured = {}

    def fake_crawl_listing(session, organiser, force=False, dry_run=False, event_limit=None):
        captured[organiser.id] = force
        return []

    monkeypatch.setattr(local_event_scraper.listing_crawler, "crawl_listing", fake_crawl_listing)
    _stub_crawl_event(monkeypatch)

    local_event_scraper.run(organiser_id=1)

    assert captured == {1: False, 2: False}


def test_force_refresh_crawls_every_returned_url_with_force_check_mode(monkeypatch):
    # crawl_listing itself is what decides "every URL" vs "just new ones" (see
    # listing_crawler.py) - from run()'s perspective, whatever it returns (existing
    # URLs included, once force=True) simply gets crawled, same as any other URL list.
    _stub_new_urls(monkeypatch, {
        1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"],
    })
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(organiser_id=1, force_refresh=True)

    assert calls == [
        (1, "https://acme.example.com/event/a"),
        (1, "https://acme.example.com/event/b"),
    ]
    # Every call used check_mode="force", regardless of the (default) --check-mode.
    assert calls.check_modes == ["force", "force"]


def test_force_refresh_overrides_explicit_check_mode(monkeypatch):
    # force_refresh wins even if a caller also passed a specific check_mode - the
    # whole point of --force-refresh is to ignore both hash-check's and url-check's
    # skip shortcuts for this run.
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a"]})
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(organiser_id=1, check_mode="url-check", force_refresh=True)

    assert calls.check_modes == ["force"]


def test_normal_run_uses_hash_check_by_default(monkeypatch):
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a"]})
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run(organiser_id=1)

    assert calls.check_modes == ["hash-check"]


# ---------------------------------------------------------------------------
# One organiser's listing-crawl failure must cost only that organiser, not the
# whole batch run - the reported incident: limelightsportsgroup.com's DNS no
# longer resolves at all, so listing_crawler.crawl_listing() (crawl4ai AND
# Firecrawl both exhausting their retries underneath it) raised all the way
# out of run() uncaught, since - unlike the per-event loop just below it -
# this listing-discovery step had no try/except of its own.
# ---------------------------------------------------------------------------

def test_listing_crawl_failure_for_one_organiser_does_not_crash_the_run(monkeypatch, capsys):
    def fake_crawl_listing(session, organiser, force=False, dry_run=False, event_limit=None):
        if organiser.id == 1:
            raise RuntimeError("DNS resolution failed for hostname \"limelightsportsgroup.com\"")
        return ["https://beta.example.com/event/d"]

    monkeypatch.setattr(local_event_scraper.listing_crawler, "crawl_listing", fake_crawl_listing)
    calls = _stub_crawl_event(monkeypatch)

    local_event_scraper.run()  # must not raise

    # Acme (organiser 1) never got as far as crawling any event...
    assert calls == [(2, "https://beta.example.com/event/d")]
    # ...but Beta (organiser 2), later in the same run, still got processed normally.
    out = capsys.readouterr().out
    assert "ERROR: Acme: listing crawl failed: RuntimeError:" in out


def test_listing_crawl_connection_error_still_stops_the_whole_run(monkeypatch):
    # Same policy the per-event loop already applies (see crawl_event's own comment):
    # a real "can't reach Firecrawl/crawl4ai's backend at all" ConnectionError is a
    # global problem, not a per-organiser one, and must still abort the run rather
    # than uselessly retrying every remaining organiser one by one.
    import requests

    def fake_crawl_listing(session, organiser, force=False, dry_run=False, event_limit=None):
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(local_event_scraper.listing_crawler, "crawl_listing", fake_crawl_listing)
    _stub_crawl_event(monkeypatch)

    with pytest.raises(requests.exceptions.ConnectionError):
        local_event_scraper.run()


# ---------------------------------------------------------------------------
# The except clause added above for the DNS-failure incident had its own bug,
# caught on the very next real run: it read organiser.name from INSIDE the
# except block. session_scope (db.py) rolls back on any exception, and
# Session.rollback() expires every attribute of every object loaded in that
# session - regardless of expire_on_commit=False, which only governs commit -
# so `organiser` (re-fetched via session.get() inside the failed `with` block)
# is both expired and detached (its session already closed) by the time the
# except block runs. Touching organiser.name there tries to lazily refresh an
# expired attribute against a session that no longer exists ->
# DetachedInstanceError - a second real crash from the very fix meant to stop
# the first one.
#
# test_listing_crawl_failure_for_one_organiser_does_not_crash_the_run above
# could not have caught this: _FakeSession.get() just returns a plain,
# never-attached Organiser straight out of a dict - it has no real
# SQLAlchemy session lifecycle to expire/detach it, so organiser.name always
# trivially works there regardless of whether the real code is correct. This
# test instead exercises a *real* SQLAlchemy session going through the same
# rollback+close sequence session_scope actually performs, against a
# throwaway model (not the real Organiser - its Postgres-only ARRAY column
# can't be created on SQLite, see test_export_events.py's own docstring on
# this) that's otherwise shaped the same way for this purpose: an object
# fetched inside a session, whose session then fails and rolls back.
# ---------------------------------------------------------------------------

def test_reading_an_attribute_after_session_scope_rollback_raises_detached_error():
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.orm import DeclarativeBase, sessionmaker
    from sqlalchemy.orm.exc import DetachedInstanceError

    class _Base(DeclarativeBase):
        pass

    class _Widget(_Base):
        __tablename__ = "widgets"
        id = Column(Integer, primary_key=True)
        name = Column(String(50))

    engine = create_engine("sqlite:///:memory:")
    _Base.metadata.create_all(engine)
    # expire_on_commit=False, matching db.py's real SessionLocal exactly - the point is
    # that this flag does NOT save you from a rollback's own expiration, only a commit's.
    real_session_local = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with real_session_local() as setup:
        setup.add(_Widget(id=1, name="Acme"))
        setup.commit()

    # Mirrors db.py's session_scope exactly: yield, commit on success, rollback + close
    # on any exception.
    @contextmanager
    def fake_session_scope():
        session = real_session_local()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    widget_from_before = None
    name_captured_before_the_risky_block = None
    try:
        with fake_session_scope() as session:
            widget_from_before = session.get(_Widget, 1)
            name_captured_before_the_risky_block = widget_from_before.name  # the fix's own technique
            raise RuntimeError("simulated listing-crawl failure")
    except RuntimeError:
        pass

    # This is the exact mistake the incident's fix made: touching the ORM object's
    # attribute from the except block, after the failed `with` block already rolled
    # back and closed its session.
    with pytest.raises(DetachedInstanceError):
        widget_from_before.name

    # This is the actual fix: a plain value captured *before* the risky block, while
    # the object was still safely attached, survives just fine afterwards - it's just
    # a string by then, no session involved at all.
    assert name_captured_before_the_risky_block == "Acme"
