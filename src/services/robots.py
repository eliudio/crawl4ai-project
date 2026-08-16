"""robots.txt compliance check, run before every listing/event/sitemap fetch."""

import threading
import time
from functools import lru_cache
from urllib.parse import urlparse

import requests
from protego import Protego

from services.config import settings

_TIMEOUT = 10
# A misconfigured/hostile robots.txt (e.g. "Crawl-delay: 600") shouldn't stall the
# whole pipeline on one organiser - safety cap, not a tuning knob.
_CRAWL_DELAY_CAP_SECONDS = 30.0

# Domain root -> monotonic time of the last request wait_for_crawl_delay() observed
# for that domain - lets repeated fetches of the very same URL (pagination replays,
# "load more" rounds, a sitemap index's sub-sitemap fetch) still space themselves at
# least Crawl-delay apart, not just the first request to a given listing/event.
_last_request_at: dict[str, float] = {}
_last_request_lock = threading.Lock()


def _domain_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


@lru_cache(maxsize=256)
def _parser_for(domain_root: str) -> Protego:
    """
    Protego (not stdlib urllib.robotparser) - it understands the wildcard (`*`) and
    end-of-url anchor (`$`) syntax real-world robots.txt files actually use (e.g.
    "Disallow: /*.pdf$"), which RobotFileParser silently mismatches since it only
    implements the original 1996 draft spec. Also exposes crawl_delay() (see
    wait_for_crawl_delay below), which RobotFileParser has no equivalent for.
    """
    url = f"{domain_root}/robots.txt"
    try:
        response = requests.get(url, headers={"User-Agent": settings.user_agent}, timeout=_TIMEOUT)
    except requests.exceptions.RequestException:
        # Unreachable (DNS, timeout, connection refused, ...) - fail open rather than
        # blocking crawls on a network hiccup, mirroring how most well-behaved
        # crawlers degrade when a site has no reachable robots.txt at all.
        return Protego.parse("")

    if response.status_code >= 500:
        # A reachable server actively erroring on its own robots.txt is a different
        # case from "no robots.txt exists" - mirrors Google's own crawlers, which
        # treat a persistent 5xx as "assume fully disallowed for now" rather than
        # "assume no rules", since the site itself couldn't tell us what's allowed.
        return Protego.parse("User-agent: *\nDisallow: /")

    if response.status_code >= 400:
        # 404 (no robots.txt at all) and other 4xx (401/403 - usually a WAF blocking
        # non-browser user agents, not a deliberate "crawl nothing" signal) both mean
        # "no rules to enforce", not disallow-all.
        return Protego.parse("")

    return Protego.parse(response.text)


def is_allowed(url: str) -> bool:
    if not settings.respect_robots_txt:
        return True
    return _parser_for(_domain_root(url)).can_fetch(url, settings.user_agent)


def wait_for_crawl_delay(url: str) -> None:
    """
    Sleeps whatever's left of this domain's own Crawl-delay (if it declares one)
    since the last request seen for that domain - call this immediately before
    every actual outbound request to `url`'s domain (scraper_client.scrape,
    sitemap_crawler's direct requests.get, ...), not just once per higher-level
    "process this URL" decision, since pagination/"load more" can fetch the very
    same listing URL many times in a row.
    """
    if not settings.respect_robots_txt:
        return

    delay = _parser_for(_domain_root(url)).crawl_delay(settings.user_agent)
    if not delay:
        return
    delay = min(delay, _CRAWL_DELAY_CAP_SECONDS)

    domain_root = _domain_root(url)
    with _last_request_lock:
        last = _last_request_at.get(domain_root)
        now = time.monotonic()
        if last is not None:
            remaining = delay - (now - last)
            if remaining > 0:
                time.sleep(remaining)
        _last_request_at[domain_root] = time.monotonic()
