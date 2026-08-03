"""robots.txt compliance check, run before every listing/event fetch."""

from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from services.config import settings


@lru_cache(maxsize=256)
def _parser_for(domain_root: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.set_url(f"{domain_root}/robots.txt")
    try:
        parser.read()
    except Exception:
        # If robots.txt is unreachable, fail open rather than blocking crawls
        # on a network hiccup — this mirrors how most well-behaved crawlers
        # degrade when a site has no reachable robots.txt at all.
        parser.disallow_all = False
    return parser


def is_allowed(url: str) -> bool:
    if not settings.respect_robots_txt:
        return True
    parsed = urlparse(url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"
    return _parser_for(domain_root).can_fetch(settings.user_agent, url)
