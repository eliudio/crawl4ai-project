"""
Unit tests for services/robots.py - the robots.txt compliance check and
Crawl-delay throttle every event/listing/sitemap fetch goes through (see
event_crawler.py, listing_crawler.py, sitemap_crawler.py, scraper_client.py).

No real network: requests.get is monkeypatched with canned responses, same
convention as sitemap_crawler.py's/discover_sitemaps.py's own tests.
"""

import pytest
import requests

from services import robots
from services.config import settings


@pytest.fixture(autouse=True)
def _stub_robots_network():
    """
    Overrides conftest.py's own same-named fixture (pytest's documented
    override-by-name mechanism) - THIS module exercises the real
    _parser_for/is_allowed/wait_for_crawl_delay logic, with requests.get
    mocked at the network boundary instead (see _fake_get below). Clears
    both the domain-parser cache and the crawl-delay throttle state before
    and after each test, since both are module-level globals shared across
    the whole test run - without this, one test's fake robots.txt for
    "https://example.com" would leak into the next.
    """
    robots._parser_for.cache_clear()
    robots._last_request_at.clear()
    yield
    robots._parser_for.cache_clear()
    robots._last_request_at.clear()


def _fake_get(robots_txt_by_domain: dict[str, tuple[int, str]]):
    """domain_root -> (status_code, body). A domain missing from the dict raises,
    same as a real connection failure."""
    def fake_get(url, headers=None, timeout=None):
        domain_root = url.rsplit("/robots.txt", 1)[0]
        if domain_root not in robots_txt_by_domain:
            raise requests.ConnectionError(f"no fake response for {url}")
        status_code, body = robots_txt_by_domain[domain_root]
        response = requests.Response()
        response.status_code = status_code
        response._content = body.encode("utf-8")
        return response
    return fake_get


# ---------------------------------------------------------------------------
# is_allowed - basic Disallow/Allow, including the modern wildcard (`*`) and
# end-of-url anchor (`$`) syntax stdlib urllib.robotparser doesn't understand
# (the whole reason this module uses Protego instead).
# ---------------------------------------------------------------------------

def test_disallowed_path_is_blocked(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nDisallow: /private/\n"),
    }))
    assert robots.is_allowed("https://example.com/private/page") is False
    assert robots.is_allowed("https://example.com/public/page") is True


def test_wildcard_and_end_anchor_are_honoured(monkeypatch):
    # "Disallow: /*.pdf$" is a de-facto convention (Google/Bing) the stdlib
    # urllib.robotparser - the original 1996-spec parser - does not implement.
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nDisallow: /*.pdf$\n"),
    }))
    assert robots.is_allowed("https://example.com/brochure.pdf") is False
    # $ anchors to end-of-url - a query string after .pdf no longer matches.
    assert robots.is_allowed("https://example.com/brochure.pdf?ref=1") is True


def test_named_user_agent_rule_takes_precedence_over_wildcard(monkeypatch):
    robots_txt = (
        "User-agent: *\nDisallow: /\n"
        f"User-agent: {settings.user_agent}\nDisallow:\n"
    )
    monkeypatch.setattr(robots.requests, "get", _fake_get({"https://example.com": (200, robots_txt)}))
    # Everyone else is blocked entirely, but this project's own configured
    # user agent has its own, more permissive rule.
    assert robots.is_allowed("https://example.com/anything") is True


def test_respect_robots_txt_disabled_allows_everything(monkeypatch):
    monkeypatch.setattr(settings, "respect_robots_txt", False)
    monkeypatch.setattr(
        robots.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch robots.txt when disabled")),
    )
    assert robots.is_allowed("https://example.com/private/anything") is True


# ---------------------------------------------------------------------------
# registrator - see Organiser.registrator's own docstring: "bot" (the default) always
# respects robots.txt; any other value names a real person with the site owner's own
# separately-obtained permission, and skips the check entirely.
# ---------------------------------------------------------------------------

def test_non_bot_registrator_skips_the_check_entirely(monkeypatch):
    monkeypatch.setattr(
        robots.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch robots.txt for a non-bot registrator")),
    )
    assert robots.is_allowed("https://example.com/private/anything", registrator="jane_doe") is True


def test_bot_registrator_is_the_default_and_still_disallowed(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nDisallow: /private/\n"),
    }))
    assert robots.is_allowed("https://example.com/private/page") is False
    assert robots.is_allowed("https://example.com/private/page", registrator="bot") is False


def test_falsy_registrator_treated_as_bot_not_as_skip(monkeypatch):
    # A not-yet-flushed Organiser ORM instance has registrator=None until its Python-side
    # default= is applied at insert - must fail safe (still check robots.txt), not be
    # mistaken for a resolved human override.
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nDisallow: /private/\n"),
    }))
    assert robots.is_allowed("https://example.com/private/page", registrator=None) is False
    assert robots.is_allowed("https://example.com/private/page", registrator="") is False


def test_parser_cached_per_domain_not_refetched(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = b"User-agent: *\nDisallow: /private/\n"
        return response

    monkeypatch.setattr(robots.requests, "get", fake_get)

    assert robots.is_allowed("https://example.com/a") is True
    assert robots.is_allowed("https://example.com/b") is True
    assert robots.is_allowed("https://example.com/private/c") is False

    assert calls == ["https://example.com/robots.txt"]  # fetched once, reused for every later check


# ---------------------------------------------------------------------------
# Fetch-failure handling - 404/other 4xx (no robots.txt / a WAF) vs. 5xx (the
# site's own server erroring) vs. genuinely unreachable (DNS/timeout/refused).
# ---------------------------------------------------------------------------

def test_404_means_no_restrictions(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({"https://example.com": (404, "")}))
    assert robots.is_allowed("https://example.com/anything") is True


def test_403_treated_as_no_restrictions_not_disallow_all(monkeypatch):
    # A 403 on robots.txt is usually a WAF blocking non-browser user agents,
    # not a deliberate "crawl nothing" signal.
    monkeypatch.setattr(robots.requests, "get", _fake_get({"https://example.com": (403, "")}))
    assert robots.is_allowed("https://example.com/anything") is True


def test_5xx_treated_as_disallow_all(monkeypatch):
    # The site itself couldn't tell us its rules - assume the conservative
    # "fully disallowed for now", not "no rules to enforce".
    monkeypatch.setattr(robots.requests, "get", _fake_get({"https://example.com": (503, "")}))
    assert robots.is_allowed("https://example.com/anything") is False


def test_unreachable_robots_txt_fails_open(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(robots.requests, "get", fake_get)
    assert robots.is_allowed("https://example.com/anything") is True


# ---------------------------------------------------------------------------
# wait_for_crawl_delay - throttling based on a site's own Crawl-delay
# directive, per domain, across repeated fetches of the very same URL
# (pagination replays, "load more" rounds, a sitemap index's sub-sitemap).
# ---------------------------------------------------------------------------

def test_no_crawl_delay_declared_does_not_sleep(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nDisallow: /private/\n"),
    }))
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))

    robots.wait_for_crawl_delay("https://example.com/a")
    robots.wait_for_crawl_delay("https://example.com/b")

    assert slept == []


def test_crawl_delay_throttles_repeated_requests_to_same_domain(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nCrawl-delay: 5\n"),
    }))
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))
    fake_now = [1000.0]
    monkeypatch.setattr(robots.time, "monotonic", lambda: fake_now[0])

    robots.wait_for_crawl_delay("https://example.com/a")  # first request - nothing to wait for yet
    assert slept == []

    fake_now[0] += 2.0  # only 2s have passed, site wants 5s between requests
    robots.wait_for_crawl_delay("https://example.com/b")
    assert slept == [3.0]

    slept.clear()
    fake_now[0] += 5.0  # a full 5s has passed since the last request
    robots.wait_for_crawl_delay("https://example.com/c")
    assert slept == []


def test_crawl_delay_is_per_domain(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://slow.example.com": (200, "User-agent: *\nCrawl-delay: 10\n"),
        "https://fast.example.com": (200, "User-agent: *\n"),
    }))
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))
    fake_now = [1000.0]
    monkeypatch.setattr(robots.time, "monotonic", lambda: fake_now[0])

    robots.wait_for_crawl_delay("https://slow.example.com/a")
    fake_now[0] += 0.1
    # A different domain's own request right after must not be throttled by
    # slow.example.com's crawl-delay.
    robots.wait_for_crawl_delay("https://fast.example.com/a")

    assert slept == []


def test_crawl_delay_is_capped(monkeypatch):
    # A misconfigured/hostile robots.txt shouldn't stall the whole pipeline.
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nCrawl-delay: 600\n"),
    }))
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(robots.time, "monotonic", lambda: 1000.0)

    robots.wait_for_crawl_delay("https://example.com/a")
    robots.wait_for_crawl_delay("https://example.com/b")  # immediately after (no time elapsed)

    assert slept == [robots._CRAWL_DELAY_CAP_SECONDS]


def test_respect_robots_txt_disabled_skips_throttling(monkeypatch):
    monkeypatch.setattr(settings, "respect_robots_txt", False)
    monkeypatch.setattr(
        robots.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch robots.txt when disabled")),
    )
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))

    robots.wait_for_crawl_delay("https://example.com/a")

    assert slept == []


def test_crawl_delay_applies_regardless_of_registrator(monkeypatch):
    # Deliberate: registrator only gates the allow/disallow check (is_allowed) - a
    # non-"bot" registrator does not also exempt requests from Crawl-delay pacing, since
    # that's about server load, not about who authorised the access. wait_for_crawl_delay
    # doesn't even take a registrator argument - this documents that it isn't meant to.
    monkeypatch.setattr(robots.requests, "get", _fake_get({
        "https://example.com": (200, "User-agent: *\nCrawl-delay: 5\n"),
    }))
    slept = []
    monkeypatch.setattr(robots.time, "sleep", lambda s: slept.append(s))
    fake_now = [1000.0]
    monkeypatch.setattr(robots.time, "monotonic", lambda: fake_now[0])

    robots.wait_for_crawl_delay("https://example.com/a")
    fake_now[0] += 2.0
    robots.wait_for_crawl_delay("https://example.com/b")

    assert slept == [3.0]
