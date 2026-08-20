"""
Domain/junk-pattern link filtering shared by listing_crawler.py (links found
by opening and clicking through a listing page) and sitemap_crawler.py
(links read straight out of a sitemap) - split out on its own so the two
don't import from each other.
"""

from urllib.parse import urlparse

_JUNK_SUBSTRINGS = [
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com",
    "mailto:", "tel:", "javascript:",
    "/privacy", "/terms", "/cookie", "/login", "/signin", "/signup",
    "/account", "/cart", "/checkout", "/basket",
]


def _strip_www(netloc: str) -> str:
    return netloc[4:] if netloc.startswith("www.") else netloc


def _core_label(netloc: str) -> str:
    """First label of a (www-stripped) domain - approximates an organiser's
    own brand name (e.g. 'zigzagrunning' from 'zigzagrunning.co.uk') without
    needing a public-suffix list."""
    return netloc.split(".")[0]


def _same_site(url: str, homepage_netloc: str) -> bool:
    netloc = _strip_www(urlparse(url).netloc.lower())
    homepage_netloc = _strip_www(homepage_netloc.lower())
    if netloc == homepage_netloc or netloc.endswith("." + homepage_netloc):
        return True
    # Some organisers' actual event pages live on a third-party ticketing
    # platform under a branded subdomain (e.g. zigzagrunning.eventrac.co.uk
    # for homepage zigzagrunning.co.uk) rather than the organiser's own
    # domain - a domain/subdomain match alone misses these entirely. Treat it
    # as the same site if the candidate's leftmost label is the organiser's
    # own brand name, regardless of what domain it's a subdomain of.
    return _core_label(netloc) == _core_label(homepage_netloc)


def filter_candidate_links(links: list[str], homepage_url: str) -> list[str]:
    homepage_netloc = urlparse(homepage_url).netloc
    seen: set[str] = set()
    candidates: list[str] = []
    for url in links:
        url = url.strip()
        if not url or url in seen:
            continue
        lower = url.lower()
        if any(junk in lower for junk in _JUNK_SUBSTRINGS):
            continue
        if not _same_site(url, homepage_netloc):
            continue
        seen.add(url)
        candidates.append(url)
    return candidates
