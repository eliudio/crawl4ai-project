"""
Unit tests for the three genuinely different ways listing_crawler._analyze_page
reaches a listing's events (see listing_crawler.py's module docstring):

- "append": a same-URL "Load more" button that grows the page (e.g.
  runthrough.co.uk, zigzagrunning.co.uk, sportivaevents.co.uk).
- "paginate": a same-URL numbered/"Next" pager with no real href that
  replaces the page's items each press (e.g. raceforlife.cancerresearchuk.org's
  faceted-search listing).
- "none": a single page with everything already on it, no interaction
  needed (e.g. itsgrimupnorthrunning.co.uk).

No real network/LLM calls: firecrawl_client.scrape and the two llm_extractor
probes are monkeypatched with canned responses, so these run instantly and
for free, and only exercise listing_crawler's own routing/looping logic.
"""

import pytest
from bs4 import BeautifulSoup

from services import firecrawl_client, listing_crawler, llm_extractor
from services.listing_crawler import (
    _APPEND_TEXT_KEYWORDS,
    _find_click_selector,
    _resolve_click_selector,
)
from services.models import Organiser


class _FakeFirecrawl:
    """
    Stands in for firecrawl_client.scrape. `pages[i]` is the (markdown,
    links, html) state of the listing page after `i` "load more"/"next"
    presses - index 0 is the very first, unclicked load. Presses are
    inferred from `actions` the same way listing_crawler builds them
    (`_click_actions`: one click + one wait per press), so this works
    unmodified for both the "append" round-loop and the "paginate" click
    replay, which both send actions this same way.
    """

    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls: list[int] = []

    def scrape(self, url, want_links=False, want_html=False, actions=None):
        presses = (len(actions) // 2) if actions else 0
        self.calls.append(presses)
        page = self.pages[min(presses, len(self.pages) - 1)]
        markdown = page["markdown"]
        links = page["links"] if want_links else []
        html = page.get("html", "") if want_html else ""
        return markdown, links, html, url


def _fake_analyze_listing_page(page_url, markdown, candidate_links):
    """Confirms any candidate that looks like an event detail page - good
    enough for these tests, which control exactly what candidates look like."""
    return {
        "event_urls": [url for url in candidate_links if "/event/" in url],
        "next_page_url": None,
    }


def _patch_firecrawl(monkeypatch, pages: list[dict]) -> _FakeFirecrawl:
    fake = _FakeFirecrawl(pages)
    monkeypatch.setattr(firecrawl_client, "scrape", fake.scrape)
    return fake


def _patch_analyze(monkeypatch):
    monkeypatch.setattr(llm_extractor, "analyze_listing_page", _fake_analyze_listing_page)


# ---------------------------------------------------------------------------
# Case 1: "append" (Load More) - runthrough.co.uk, zigzagrunning.co.uk,
# sportivaevents.co.uk each render a couple of events up front, then reveal
# more underneath after each click, then stop growing once exhausted.
# ---------------------------------------------------------------------------

_LOAD_MORE_CASES = [
    (
        "https://www.runthrough.co.uk/events-timeline",
        "https://www.runthrough.co.uk/",
        [
            {"markdown": "m0", "links": [
                "https://www.runthrough.co.uk/event/newark-half-marathon",
                "https://www.runthrough.co.uk/event/frome-running-festival",
            ], "html": "<button class='load-more'>Load More</button>"},
            {"markdown": "m1", "links": [
                "https://www.runthrough.co.uk/event/newark-half-marathon",
                "https://www.runthrough.co.uk/event/frome-running-festival",
                "https://www.runthrough.co.uk/event/beverley-half-marathon",
                "https://www.runthrough.co.uk/event/swindon-half-marathon",
            ], "html": "<button class='load-more'>Load More</button>"},
        ],
        {
            "https://www.runthrough.co.uk/event/newark-half-marathon",
            "https://www.runthrough.co.uk/event/frome-running-festival",
            "https://www.runthrough.co.uk/event/beverley-half-marathon",
            "https://www.runthrough.co.uk/event/swindon-half-marathon",
        },
    ),
    (
        "https://www.zigzagrunning.co.uk/",
        "https://www.zigzagrunning.co.uk/",
        [
            {"markdown": "m0", "links": [
                "https://www.zigzagrunning.co.uk/event/spring-10k",
            ], "html": "<button class='show-more'>Show more</button>"},
            {"markdown": "m1", "links": [
                "https://www.zigzagrunning.co.uk/event/spring-10k",
                "https://www.zigzagrunning.co.uk/event/summer-trail-run",
            ], "html": "<button class='show-more'>Show more</button>"},
            {"markdown": "m2", "links": [
                "https://www.zigzagrunning.co.uk/event/spring-10k",
                "https://www.zigzagrunning.co.uk/event/summer-trail-run",
                "https://www.zigzagrunning.co.uk/event/autumn-marathon",
            ], "html": "<button class='show-more'>Show more</button>"},
        ],
        {
            "https://www.zigzagrunning.co.uk/event/spring-10k",
            "https://www.zigzagrunning.co.uk/event/summer-trail-run",
            "https://www.zigzagrunning.co.uk/event/autumn-marathon",
        },
    ),
    (
        "https://sportivaevents.co.uk/events/",
        "https://sportivaevents.co.uk/",
        [
            {"markdown": "m0", "links": [
                "https://sportivaevents.co.uk/event/hill-climb-classic",
            ], "html": "<button class='view-more'>View more</button>"},
            {"markdown": "m1", "links": [
                "https://sportivaevents.co.uk/event/hill-climb-classic",
                "https://sportivaevents.co.uk/event/coastal-century-ride",
            ], "html": "<button class='view-more'>View more</button>"},
        ],
        {
            "https://sportivaevents.co.uk/event/hill-climb-classic",
            "https://sportivaevents.co.uk/event/coastal-century-ride",
        },
    ),
]


@pytest.mark.parametrize("page_url, homepage_url, pages, expected_events", _LOAD_MORE_CASES)
def test_load_more_site_grows_until_exhausted(monkeypatch, page_url, homepage_url, pages, expected_events):
    fake = _patch_firecrawl(monkeypatch, pages)
    _patch_analyze(monkeypatch)
    monkeypatch.setattr(
        llm_extractor, "detect_load_more",
        lambda url, html: {"interaction_type": "append", "load_more_selector": ".load-more"},
    )

    event_urls, candidates, next_page_url = listing_crawler._analyze_page(page_url, homepage_url)

    assert set(event_urls) == expected_events
    assert next_page_url is None
    # Round 0 (unclicked) plus at least one click round that grew the page,
    # plus the final round that found nothing new and stopped.
    assert len(fake.calls) >= 2


# ---------------------------------------------------------------------------
# Case 2: "paginate" (Next-page) - raceforlife.cancerresearchuk.org's
# faceted-search listing replaces its visible items each press rather than
# growing, so each page's events have to be unioned rather than just reading
# the last one.
# ---------------------------------------------------------------------------

def test_paginate_site_unions_events_across_pages(monkeypatch):
    page_url = "https://raceforlife.cancerresearchuk.org/find-an-event?size=n_200_n&sort-field=eventDate"
    homepage_url = "https://raceforlife.cancerresearchuk.org/"
    pages = [
        {"markdown": "m0", "links": [
            "https://raceforlife.cancerresearchuk.org/event/hyde-park-5k",
            "https://raceforlife.cancerresearchuk.org/event/heaton-park-10k",
        ], "html": "<nav class='pager'><a class='next'>Next</a></nav>"},
        {"markdown": "m1", "links": [
            "https://raceforlife.cancerresearchuk.org/event/roundhay-park-5k",
            "https://raceforlife.cancerresearchuk.org/event/bute-park-10k",
        ]},
        {"markdown": "m2", "links": [
            "https://raceforlife.cancerresearchuk.org/event/roundhay-park-5k",
            "https://raceforlife.cancerresearchuk.org/event/bute-park-10k",
        ]},
    ]
    fake = _patch_firecrawl(monkeypatch, pages)
    _patch_analyze(monkeypatch)
    monkeypatch.setattr(
        llm_extractor, "detect_load_more",
        lambda url, html: {"interaction_type": "paginate", "load_more_selector": "a.next"},
    )

    event_urls, candidates, next_page_url = listing_crawler._analyze_page(page_url, homepage_url)

    assert set(event_urls) == {
        "https://raceforlife.cancerresearchuk.org/event/hyde-park-5k",
        "https://raceforlife.cancerresearchuk.org/event/heaton-park-10k",
        "https://raceforlife.cancerresearchuk.org/event/roundhay-park-5k",
        "https://raceforlife.cancerresearchuk.org/event/bute-park-10k",
    }
    # A same-URL JS pager has no real href to a further page - every
    # reachable page was already walked inside _analyze_page itself.
    assert next_page_url is None
    # Page 1 (round 0) + page 2 (1 click) + page 3 (2 clicks, nothing new, stop).
    assert fake.calls == [0, 1, 2]


# ---------------------------------------------------------------------------
# Case 3: "none" (single page) - itsgrimupnorthrunning.co.uk shows every
# event on first load, no interaction of any kind needed.
# ---------------------------------------------------------------------------

def test_single_page_site_needs_no_interaction(monkeypatch):
    page_url = "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events"
    homepage_url = "https://www.itsgrimupnorthrunning.co.uk/"
    pages = [
        {"markdown": "m0", "links": [
            "https://www.itsgrimupnorthrunning.co.uk/event/pennine-way-ultra",
            "https://www.itsgrimupnorthrunning.co.uk/event/moor-trail-10k",
            "https://www.itsgrimupnorthrunning.co.uk/event/dales-half-marathon",
        ], "html": "<div>no load-more or pager here</div>"},
    ]
    fake = _patch_firecrawl(monkeypatch, pages)
    _patch_analyze(monkeypatch)
    monkeypatch.setattr(
        llm_extractor, "detect_load_more",
        lambda url, html: {"interaction_type": "none", "load_more_selector": None},
    )

    event_urls, candidates, next_page_url = listing_crawler._analyze_page(page_url, homepage_url)

    assert set(event_urls) == {
        "https://www.itsgrimupnorthrunning.co.uk/event/pennine-way-ultra",
        "https://www.itsgrimupnorthrunning.co.uk/event/moor-trail-10k",
        "https://www.itsgrimupnorthrunning.co.uk/event/dales-half-marathon",
    }
    assert next_page_url is None
    # No load-more/pager - exactly the one, unclicked scrape.
    assert fake.calls == [0]


# ---------------------------------------------------------------------------
# _discover_listing_urls - robots.txt must gate the very first homepage fetch
# too (an organiser with no listing_urls yet), not just the listing-page loop
# _analyze_page/_crawl_one_listing_url already checks.
# ---------------------------------------------------------------------------

class _FakeSession:
    """Only .add() is ever called by _discover_listing_urls - no real DB needed."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)


def test_discover_listing_urls_respects_robots_disallow(monkeypatch, capsys):
    organiser = Organiser(homepage_url="https://example.com/")
    session = _FakeSession()

    monkeypatch.setattr(listing_crawler.robots, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        listing_crawler.scraper_client, "scrape",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not scrape a robots-disallowed homepage")),
    )

    result = listing_crawler._discover_listing_urls(session, organiser)

    assert result == []
    assert session.added == []  # organiser.listing_urls was never even touched/persisted
    # A robots skip must print its own grep-able marker - otherwise it's
    # indistinguishable in the log from a genuine scrape failure.
    assert "ROBOTS-SKIP: https://example.com/ (listing discovery)" in capsys.readouterr().out


def test_discover_listing_urls_scrapes_when_allowed(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    session = _FakeSession()

    monkeypatch.setattr(listing_crawler.robots, "is_allowed", lambda url, registrator="bot": True)
    monkeypatch.setattr(
        listing_crawler.scraper_client, "scrape",
        lambda url, want_links=False: ("markdown", ["https://example.com/events"], "", url),
    )
    monkeypatch.setattr(listing_crawler, "filter_candidate_links", lambda links, homepage: links)
    monkeypatch.setattr(
        listing_crawler.llm_extractor, "discover_listing_urls",
        lambda homepage, markdown, candidates: candidates,
    )

    result = listing_crawler._discover_listing_urls(session, organiser)

    assert result == ["https://example.com/events"]
    assert organiser.listing_urls == ["https://example.com/events"]
    assert session.added == [organiser]


def test_crawl_one_listing_url_respects_robots_disallow_and_logs_it(monkeypatch, capsys):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    session = _FakeSession()

    monkeypatch.setattr(listing_crawler.robots, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        listing_crawler, "_analyze_page",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not analyze a robots-disallowed listing page")),
    )

    new_urls = listing_crawler._crawl_one_listing_url(
        session, organiser, "https://example.com/events", seen=set(),
    )

    assert new_urls == []
    # Same grep-able marker as the homepage-discovery skip above - a listing
    # page skipped mid-pagination must be just as visible in the log.
    assert "ROBOTS-SKIP: https://example.com/events (listing)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# force=True: local_event_scraper.py's --force-refresh needs every confirmed event
# URL back, not just ones missing from the database - see event_crawler.py's
# check_mode="force", which then re-extracts and replaces each one in place.
# ---------------------------------------------------------------------------

class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExistingUrlsSession:
    """.scalars() returns whichever URLs the test configures as 'already stored',
    regardless of the actual query object passed in; .add() (every CrawlRun audit
    row _crawl_one_listing_url records, success or failure) is just discarded -
    no real DB needed for either."""

    def __init__(self, existing_urls):
        self._existing_urls = existing_urls

    def scalars(self, query):
        return _FakeScalarResult(self._existing_urls)

    def add(self, obj):
        pass


def _stub_two_confirmed_events(monkeypatch):
    monkeypatch.setattr(listing_crawler.robots, "is_allowed", lambda url, registrator="bot": True)
    monkeypatch.setattr(
        listing_crawler, "_analyze_page",
        lambda page_url, homepage_url: (
            ["https://example.com/event/a", "https://example.com/event/b"], [], None,
        ),
    )


def test_crawl_one_listing_url_excludes_existing_urls_by_default(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    session = _FakeExistingUrlsSession(["https://example.com/event/a"])
    _stub_two_confirmed_events(monkeypatch)

    new_urls = listing_crawler._crawl_one_listing_url(
        session, organiser, "https://example.com/events", seen=set(),
    )

    assert new_urls == ["https://example.com/event/b"]


def test_crawl_one_listing_url_force_returns_existing_urls_too(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    session = _FakeExistingUrlsSession(["https://example.com/event/a"])
    _stub_two_confirmed_events(monkeypatch)

    urls = listing_crawler._crawl_one_listing_url(
        session, organiser, "https://example.com/events", seen=set(), force=True,
    )

    assert urls == ["https://example.com/event/a", "https://example.com/event/b"]


def test_crawl_one_listing_url_force_still_respects_in_run_dedup(monkeypatch):
    # force only bypasses the "already in the database" exclusion, not the
    # within-this-run `seen` dedup (e.g. the same event linked from two
    # different listing pages during one crawl_listing() call).
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    session = _FakeExistingUrlsSession([])
    _stub_two_confirmed_events(monkeypatch)

    urls = listing_crawler._crawl_one_listing_url(
        session, organiser, "https://example.com/events",
        seen={"https://example.com/event/a"}, force=True,
    )

    assert urls == ["https://example.com/event/b"]


# ---------------------------------------------------------------------------
# crawl_listing's generic dispatch (see discovery_handlers.py): every organiser
# has exactly one Organiser.handler, looked up and called - no hardcoded
# cascade/special-casing left in crawl_listing() itself.
# ---------------------------------------------------------------------------

def test_crawl_listing_dispatches_to_the_registered_handler(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    organiser.handler = "custom_handler"
    organiser.handler_params = {"some_param": 1}
    session = _FakeExistingUrlsSession([])
    captured = {}

    def fake_handler(session_arg, organiser_arg, params, force, dry_run, event_limit):
        captured["args"] = (session_arg, organiser_arg, params, force, dry_run, event_limit)
        return ["https://example.com/handled/"]

    monkeypatch.setattr(listing_crawler.discovery_handlers, "get_handler", lambda name: fake_handler if name == "custom_handler" else None)

    urls = listing_crawler.crawl_listing(session, organiser, force=True)

    assert urls == ["https://example.com/handled/"]
    assert captured["args"] == (session, organiser, {"some_param": 1}, True, False, None)


def test_crawl_listing_passes_empty_dict_when_no_handler_params(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    organiser.handler = "default"
    organiser.handler_params = None
    session = _FakeExistingUrlsSession([])
    captured = {}

    monkeypatch.setattr(
        listing_crawler.discovery_handlers, "get_handler",
        lambda name: (lambda s, o, params, force, dry_run, event_limit: captured.setdefault("params", params) and []),
    )

    listing_crawler.crawl_listing(session, organiser)

    assert captured["params"] == {}


def test_crawl_listing_falls_back_to_default_for_unknown_handler_name(monkeypatch, capsys):
    # get_handler("no-such-handler") returns None; get_handler("default") must still
    # resolve to a real handler - monkeypatching listing_crawler.default_handler
    # wouldn't reach this at all, since the registry already holds a direct
    # reference to the original function from module-load time, not a live lookup
    # by name - so the fallback itself is stubbed via the registry lookup instead.
    organiser = Organiser(homepage_url="https://example.com/", name="Acme")
    organiser.id = 1
    organiser.handler = "no-such-handler"
    organiser.handler_params = None
    session = _FakeExistingUrlsSession([])

    def fake_get_handler(name):
        if name == "default":
            return lambda s, o, params, force, dry_run, event_limit: ["https://example.com/default-used/"]
        return None

    monkeypatch.setattr(listing_crawler.discovery_handlers, "get_handler", fake_get_handler)

    urls = listing_crawler.crawl_listing(session, organiser)

    assert urls == ["https://example.com/default-used/"]
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "no-such-handler" in out
    assert "Acme" in out


# ---------------------------------------------------------------------------
# default_handler - today's historical sitemap-then-LLM-guessed behaviour,
# now just one named, registered handler among others (see Organiser.handler).
# ---------------------------------------------------------------------------

def test_default_handler_uses_sitemap_param_when_present(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    session = _FakeExistingUrlsSession([])

    monkeypatch.setattr(
        listing_crawler, "_crawl_from_sitemap",
        lambda session_arg, organiser_arg, sitemap_url, force=False: ["https://example.com/from-sitemap/"] if sitemap_url == "https://example.com/sitemap.xml" else None,
    )
    monkeypatch.setattr(
        listing_crawler, "_discover_listing_urls",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fall back to LLM-guessed discovery")),
    )

    urls = listing_crawler.default_handler(session, organiser, {"sitemap_url": "https://example.com/sitemap.xml"})

    assert urls == ["https://example.com/from-sitemap/"]


def test_default_handler_falls_back_to_listing_urls_when_sitemap_unusable(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    organiser.listing_urls = []
    session = _FakeExistingUrlsSession([])

    monkeypatch.setattr(listing_crawler, "_crawl_from_sitemap", lambda *a, **k: None)
    monkeypatch.setattr(listing_crawler, "_discover_listing_urls", lambda session_arg, organiser_arg: [])

    urls = listing_crawler.default_handler(session, organiser, {"sitemap_url": "https://example.com/sitemap.xml"})

    assert urls == []  # nothing discovered either - just confirms it fell through without crashing


def test_default_handler_skips_sitemap_entirely_when_no_param(monkeypatch):
    organiser = Organiser(homepage_url="https://example.com/")
    organiser.id = 1
    organiser.listing_urls = []
    session = _FakeExistingUrlsSession([])

    monkeypatch.setattr(
        listing_crawler, "_crawl_from_sitemap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not attempt a sitemap with no sitemap_url param")),
    )
    monkeypatch.setattr(listing_crawler, "_discover_listing_urls", lambda session_arg, organiser_arg: [])

    listing_crawler.default_handler(session, organiser, {})  # must not raise


# ---------------------------------------------------------------------------
# _filter_new_urls - shared by any handler that gets back a whole sitemap's
# worth of URLs in one go.
# ---------------------------------------------------------------------------

def test_filter_new_urls_excludes_existing_by_default():
    session = _FakeExistingUrlsSession(["https://example.com/a"])
    urls = listing_crawler._filter_new_urls(session, ["https://example.com/a", "https://example.com/b"], force=False)
    assert urls == ["https://example.com/b"]


def test_filter_new_urls_returns_everything_when_forced():
    session = _FakeExistingUrlsSession(["https://example.com/a"])
    urls = listing_crawler._filter_new_urls(session, ["https://example.com/a", "https://example.com/b"], force=True)
    assert urls == ["https://example.com/a", "https://example.com/b"]


# ---------------------------------------------------------------------------
# _find_click_selector / _resolve_click_selector: the real bug that broke
# runthrough.co.uk twice - an LLM-guessed selector either matched dozens of
# unrelated buttons sharing the same generic class, or an attribute that
# doesn't survive to the live DOM. These lock in the deterministic,
# text-grounded replacement that actually worked against the live site.
# ---------------------------------------------------------------------------

# Mimics runthrough.co.uk's real markup: the true "Load More" button shares
# its only classes with a bunch of unrelated per-event "Book now" buttons,
# so a class-only selector can't tell them apart - only the button's own
# text and its distinctively-classed wrapper can.
_RUNTHROUGH_LIKE_HTML = """
<div class="event-card"><button class="button button-primary button-sm">Book now</button></div>
<div class="event-card"><button class="button button-primary button-sm">Book now</button></div>
<div class="event-card"><button class="button button-primary button-sm">Book now</button></div>
<div class="events__btns"><component-button classname="button button-primary">
    <button class="button button-primary">Load More</button>
</component-button></div>
"""


def test_find_click_selector_ignores_decoys_sharing_the_same_class():
    selector = _find_click_selector(_RUNTHROUGH_LIKE_HTML, _APPEND_TEXT_KEYWORDS)

    assert selector is not None
    matches = BeautifulSoup(_RUNTHROUGH_LIKE_HTML, "html.parser").select(selector)
    assert len(matches) == 1
    assert matches[0].get_text(strip=True) == "Load More"


def test_find_click_selector_none_when_text_is_ambiguous():
    html = """
    <button class="btn">Load More</button>
    <button class="btn">Load More</button>
    """
    assert _find_click_selector(html, _APPEND_TEXT_KEYWORDS) is None


def test_find_click_selector_none_when_no_text_match():
    html = "<button class='btn'>Subscribe</button>"
    assert _find_click_selector(html, _APPEND_TEXT_KEYWORDS) is None


def test_resolve_click_selector_prefers_deterministic_result_over_llm_guess():
    # The LLM's guess here is the exact non-unique selector that caused the
    # original bug - _resolve_click_selector should ignore it in favour of
    # the text-grounded one, not just validate-and-use it.
    selector = _resolve_click_selector(_RUNTHROUGH_LIKE_HTML, "append", "button.button-primary")

    matches = BeautifulSoup(_RUNTHROUGH_LIKE_HTML, "html.parser").select(selector)
    assert len(matches) == 1
    assert matches[0].get_text(strip=True) == "Load More"


def test_resolve_click_selector_falls_back_to_validated_llm_guess():
    # No "load more"/"show more"/"view more" text anywhere for the
    # deterministic finder to grab onto - falls back to the LLM's guess,
    # still only if that guess is itself unique.
    html = "<button id='pager-more'>»</button>"
    assert _resolve_click_selector(html, "append", "#pager-more") == "#pager-more"


def test_resolve_click_selector_rejects_non_unique_llm_fallback():
    html = "<button class='btn'>»</button><button class='btn'>»</button>"
    assert _resolve_click_selector(html, "append", ".btn") is None


def test_resolve_click_selector_none_for_no_interaction():
    assert _resolve_click_selector(_RUNTHROUGH_LIKE_HTML, "none", None) is None


# ---------------------------------------------------------------------------
# llm_extractor.analyze_listing_page: a deep "load more"/pager site fully
# exhausted before this is called can hand it hundreds of candidates -
# confirming them by index instead of echoing full URLs back is what keeps
# that cheap and avoids the truncated-JSON failure seen in practice (331
# candidates blew through an 8000-token cap and discarded the whole result).
# ---------------------------------------------------------------------------

def test_analyze_listing_page_resolves_indices_not_urls(monkeypatch):
    candidates = [f"https://example.com/event/{i}" for i in range(300)]

    def fake_run_llm(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens=1200):
        # Confirming by index costs a handful of characters each, nowhere
        # near what retyping 300 full URLs would take.
        assert max_tokens < 4000
        return {"event_link_indices": [0, 5, 299], "next_page_link_index": 10}

    monkeypatch.setattr(llm_extractor, "_run_llm", fake_run_llm)

    result = llm_extractor.analyze_listing_page("https://example.com/listing", "markdown", candidates)

    assert result["event_urls"] == [candidates[0], candidates[5], candidates[299]]
    assert result["next_page_url"] == candidates[10]


def test_analyze_listing_page_ignores_invalid_indices(monkeypatch):
    candidates = ["https://example.com/event/a", "https://example.com/event/b"]

    monkeypatch.setattr(
        llm_extractor, "_run_llm",
        lambda *a, **k: {"event_link_indices": [0, 99, -1, "not-an-int"], "next_page_link_index": 99},
    )

    result = llm_extractor.analyze_listing_page("https://example.com/listing", "markdown", candidates)

    assert result["event_urls"] == [candidates[0]]
    assert result["next_page_url"] is None
