"""
Stage 1 of the pipeline: given an organiser, fetch its listing/homepage and
find event detail URLs on it.

No per-site CSS selector config (unlike the old prototype's SiteConfig
approach). Instead, links are first narrowed down heuristically by domain +
junk patterns (filter_candidate_links), then the survivors are confirmed as
actual event detail pages by the LLM (llm_extractor.identify_event_links) -
the heuristic pass alone can't tell an event link apart from e.g. /about or
/results on the same domain. That's the trade that makes this scale to
hundreds of organisers without a bespoke config per site; phase 2
(aggregator-driven discovery) can layer smarter per-site heuristics on top
later if needed.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from services import firecrawl_client, llm_extractor, robots
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event, Organiser

_JUNK_SUBSTRINGS = [
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com",
    "mailto:", "tel:", "javascript:",
    "/privacy", "/terms", "/cookie", "/login", "/signin", "/signup",
    "/account", "/cart", "/checkout", "/basket",
]


def _strip_www(netloc: str) -> str:
    return netloc[4:] if netloc.startswith("www.") else netloc


def _same_site(url: str, homepage_netloc: str) -> bool:
    netloc = _strip_www(urlparse(url).netloc.lower())
    homepage_netloc = _strip_www(homepage_netloc.lower())
    return netloc == homepage_netloc or netloc.endswith("." + homepage_netloc)


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


def _discover_listing_urls(session: Session, organiser: Organiser) -> list[str]:
    """
    Inspect the homepage with AI to find where event listings live, and
    persist the result on the organiser so this only has to run once. Covers
    all three cases: events on the homepage itself, a single dedicated
    listing page, or several (e.g. per-category) listing pages.
    """
    markdown, links = firecrawl_client.scrape(organiser.homepage_url, want_links=True)
    candidates = filter_candidate_links(links, organiser.homepage_url)
    discovered = llm_extractor.discover_listing_urls(organiser.homepage_url, markdown, candidates)

    organiser.listing_urls = discovered
    session.add(organiser)
    return discovered


def crawl_listing(session: Session, organiser: Organiser) -> list[str]:
    """
    Crawl one organiser's listing page(s). Returns the event URLs that are
    new (not already stored) so the caller can decide how to hand them off
    (Pub/Sub in production, direct in-process call for local runs).
    """
    listing_urls = organiser.listing_urls or []
    if not listing_urls:
        listing_urls = _discover_listing_urls(session, organiser)
        if not listing_urls:
            return []

    new_urls: list[str] = []
    seen: set[str] = set()

    for listing_url in listing_urls:
        run = CrawlRun(
            run_type=CrawlRunType.LISTING,
            target_url=listing_url,
            organiser_id=organiser.id,
            status=CrawlStatus.FAILED,
            started_at=datetime.now(timezone.utc),
        )

        if not robots.is_allowed(listing_url):
            run.status = CrawlStatus.SKIPPED
            run.detail = "disallowed by robots.txt"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            continue

        try:
            markdown, links = firecrawl_client.scrape(listing_url, want_links=True)
            candidates = filter_candidate_links(links, organiser.homepage_url)
            event_links = llm_extractor.identify_event_links(listing_url, markdown, candidates)

            existing_urls = set(
                session.scalars(
                    select(Event.url).where(Event.url.in_(event_links))
                ).all()
            )
            page_new = [u for u in event_links if u not in existing_urls and u not in seen]
            seen.update(page_new)
            new_urls.extend(page_new)

            run.status = CrawlStatus.SUCCESS
            run.detail = f"{len(candidates)} candidate links, {len(event_links)} confirmed events, {len(page_new)} new"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
        except Exception as e:
            run.detail = f"{type(e).__name__}: {e}"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)

    return new_urls
