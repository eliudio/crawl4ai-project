"""robots.txt compliance check, run before every listing/event fetch."""

import urllib.request
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from services.config import settings


@lru_cache(maxsize=256)
def _parser_for(domain_root: str) -> RobotFileParser:
    url = f"{domain_root}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(url)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read()
        parser.parse(raw.decode("utf-8", errors="ignore").splitlines())
    except HTTPError:
        # Any HTTP error fetching robots.txt (404, 403, 401, 5xx, ...) is
        # treated as "no rules to enforce" rather than RobotFileParser's
        # stdlib default of disallow_all=True on 401/403. A 403 on
        # robots.txt is usually a WAF blocking non-browser user agents, not
        # a deliberate "crawl nothing" signal, so fail open like we do for
        # network errors below.
        parser.allow_all = True
    except Exception:
        # If robots.txt is unreachable (DNS, timeout, connection refused),
        # fail open rather than blocking crawls on a network hiccup — this
        # mirrors how most well-behaved crawlers degrade when a site has no
        # reachable robots.txt at all.
        parser.disallow_all = False
    return parser


def is_allowed(url: str) -> bool:
    if not settings.respect_robots_txt:
        return True
    parsed = urlparse(url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"
    return _parser_for(domain_root).can_fetch(settings.user_agent, url)
