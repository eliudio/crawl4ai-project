"""
Unit tests for local_runner.run()'s --mode handling:
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

from services import local_runner
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
    monkeypatch.setattr(local_runner, "init_db", lambda: None)
    monkeypatch.setattr(local_runner, "seed_from_csv", lambda: None)

    @contextmanager
    def fake_session_scope():
        yield _FakeSession(organisers)

    monkeypatch.setattr(local_runner, "session_scope", fake_session_scope)


def _stub_new_urls(monkeypatch, urls_by_organiser: dict[int, list[str]]):
    monkeypatch.setattr(
        local_runner.listing_crawler, "crawl_listing",
        lambda session, organiser, force=False: urls_by_organiser.get(organiser.id, []),
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

    monkeypatch.setattr(local_runner.event_crawler, "crawl_event", fake_crawl_event)
    return calls


def test_normal_mode_crawls_every_new_url(monkeypatch):
    _stub_new_urls(monkeypatch, {
        1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"],
        2: ["https://beta.example.com/event/c"],
    })
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run(mode="normal")

    assert calls == [
        (1, "https://acme.example.com/event/a"),
        (1, "https://acme.example.com/event/b"),
        (2, "https://beta.example.com/event/c"),
    ]


def test_dry_run_mode_never_crawls_events(monkeypatch, capsys):
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"]})
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run(mode="dry-run")

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

    local_runner.run(mode="sanity-check")

    # Exactly one event per organiser, even though Acme had three new URLs.
    assert calls == [
        (1, "https://acme.example.com/event/a"),
        (2, "https://beta.example.com/event/d"),
    ]


def test_sanity_check_mode_handles_organiser_with_no_new_urls(monkeypatch):
    _stub_new_urls(monkeypatch, {1: [], 2: ["https://beta.example.com/event/d"]})
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run(mode="sanity-check")

    assert calls == [(2, "https://beta.example.com/event/d")]


def test_default_mode_is_normal(monkeypatch):
    """run() called with no mode argument at all behaves like mode="normal"."""
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a"]})
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run()

    assert calls == [(1, "https://acme.example.com/event/a")]


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

    def fake_crawl_listing(session, organiser, force=False):
        captured[organiser.id] = force
        return []

    monkeypatch.setattr(local_runner.listing_crawler, "crawl_listing", fake_crawl_listing)
    _stub_crawl_event(monkeypatch)

    local_runner.run(organiser_id=1, force_refresh=True)

    assert captured == {1: True, 2: True}


def test_normal_run_does_not_force_crawl_listing(monkeypatch):
    captured = {}

    def fake_crawl_listing(session, organiser, force=False):
        captured[organiser.id] = force
        return []

    monkeypatch.setattr(local_runner.listing_crawler, "crawl_listing", fake_crawl_listing)
    _stub_crawl_event(monkeypatch)

    local_runner.run(organiser_id=1)

    assert captured == {1: False, 2: False}


def test_force_refresh_crawls_every_returned_url_with_force_check_mode(monkeypatch):
    # crawl_listing itself is what decides "every URL" vs "just new ones" (see
    # listing_crawler.py) - from run()'s perspective, whatever it returns (existing
    # URLs included, once force=True) simply gets crawled, same as any other URL list.
    _stub_new_urls(monkeypatch, {
        1: ["https://acme.example.com/event/a", "https://acme.example.com/event/b"],
    })
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run(organiser_id=1, force_refresh=True)

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

    local_runner.run(organiser_id=1, check_mode="url-check", force_refresh=True)

    assert calls.check_modes == ["force"]


def test_normal_run_uses_hash_check_by_default(monkeypatch):
    _stub_new_urls(monkeypatch, {1: ["https://acme.example.com/event/a"]})
    calls = _stub_crawl_event(monkeypatch)

    local_runner.run(organiser_id=1)

    assert calls.check_modes == ["hash-check"]
