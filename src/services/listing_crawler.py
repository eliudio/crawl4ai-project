"""
Stage 1 of the pipeline: given an organiser, fetch its listing/homepage and
find event detail URLs on it.

No hand-maintained per-site CSS selector config (unlike the old prototype's
SiteConfig approach, which generated + validated per-site selectors for three
loading strategies via Selenium). Instead:

- Links are first narrowed down heuristically by domain + junk patterns
  (filter_candidate_links), then the survivors are confirmed as actual event
  detail pages by the LLM (llm_extractor.analyze_listing_page) - the
  heuristic pass alone can't tell an event link apart from e.g. /about or
  /results on the same domain.
- The same LLM call also classifies how the rest of a listing's events are
  reached: a distinct next-page URL (numbered pagination - followed by
  looping `_crawl_one_listing_url` with each next_page_url in turn, capped at
  _MAX_LISTING_PAGES), or a same-URL "load more" affordance. The latter is
  handled either by clicking a selector the LLM picks out from the page's own
  HTML each time (most "Load more" buttons are click-to-AJAX, not
  scroll-triggered - scrolling past them does nothing), or by a scroll+wait
  sequence for genuine infinite-scroll pages with no distinct clickable
  element.

That's the trade that makes this scale to hundreds of organisers without a
bespoke config *maintained by us* per site - the selector is derived fresh
from the live page each crawl rather than hand-written and stored; phase 2
(aggregator-driven discovery) can layer smarter per-site heuristics on top
later if needed.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
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

# Safety caps, not tuning knobs - just there to bound a runaway pagination
# loop or a "load more" page that never stops offering more.
_MAX_LISTING_PAGES = 20
_LOAD_MORE_MAX_ROUNDS = 4
_LOAD_MORE_SCROLL_STEP_INCREMENT = 6


def _scroll_actions(steps: int) -> list[dict]:
    actions: list[dict] = []
    for _ in range(steps):
        actions.append({"type": "scroll", "direction": "down"})
        actions.append({"type": "wait", "milliseconds": 1500})
    return actions


def _click_actions(selector: str, presses: int) -> list[dict]:
    """
    Click a "load more"-style element `presses` times in one fresh page load
    (there's no persistent browser session across separate scrape() calls, so
    reaching "pressed 3 times" means clicking 3 times in a row here, not
    resuming a previous click).
    """
    actions: list[dict] = []
    for _ in range(presses):
        actions.append({"type": "click", "selector": selector})
        actions.append({"type": "wait", "milliseconds": 1500})
    return actions


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
    markdown, links, _html = firecrawl_client.scrape(organiser.homepage_url, want_links=True)
    candidates = filter_candidate_links(links, organiser.homepage_url)
    discovered = llm_extractor.discover_listing_urls(organiser.homepage_url, markdown, candidates)

    organiser.listing_urls = discovered
    session.add(organiser)
    return discovered


def _analyze_page(page_url: str, homepage_url: str) -> tuple[list[str], list[str], str | None]:
    """
    Scrape one listing page and analyze it. Returns (event_urls, all_candidates,
    next_page_url).

    If more events are reachable only by interacting with this same URL - a
    "load more" button, or numbered/"Next" pagination controls with no real
    href to follow - they add to THIS SAME page rather than navigating
    anywhere, so the page as first loaded is never the full page. This is a
    two-phase process, deliberately using two different LLM calls so event
    extraction never runs on a not-yet-fully-loaded page: phase 1 repeatedly
    calls llm_extractor.detect_load_more - a cheap probe that only checks "is
    there still a load-more affordance, and if it's a real clickable element
    (as opposed to plain infinite scroll), what CSS selector targets it" -
    and presses it (click if there's a selector; scroll for genuine infinite
    scroll, since a real 'Load more' button - e.g. WordPress plugins commonly
    used by these sites - fires an AJAX request on click and does nothing on
    scroll) until it reports the affordance is gone. Only then does phase 2
    call llm_extractor.analyze_listing_page exactly once, to actually read
    event_urls/next_page_url off that now fully-loaded page. Each scrape()
    call is a fresh page load though (no persistent browser session across
    calls), so "press/scroll further" means re-scraping with progressively
    more clicks/scroll, not resuming a previous one.

    The probe alone isn't trustworthy as a stop condition: plugins (e.g. Divi
    Machine's "Load more", seen on these sites) often leave "load more is
    enabled for this archive" markers in the DOM (a container attribute, the
    button's own markup left in place) that don't reflect whether there's
    currently anything left to load, so the LLM can keep reporting
    has_more_via_interaction=true forever even once every event is already
    showing. The actual stop condition is therefore whether pressing again
    surfaced any link not already seen - if not, the page has genuinely
    stopped changing and we treat it as exhausted regardless of what the
    probe says. _LOAD_MORE_MAX_ROUNDS is a secondary safety valve on top of
    that, not a tuning knob.
    """
    markdown, links, html = firecrawl_client.scrape(page_url, want_links=True, want_html=True)
    probe = llm_extractor.detect_load_more(page_url, html)
    seen_links = set(links)

    round_num = 0
    while probe["has_more_via_interaction"] and round_num < _LOAD_MORE_MAX_ROUNDS:
        round_num += 1
        if probe["load_more_selector"]:
            actions = _click_actions(probe["load_more_selector"], presses=round_num)
        else:
            steps = _LOAD_MORE_SCROLL_STEP_INCREMENT * (round_num + 1)
            actions = _scroll_actions(steps)

        markdown, links, html = firecrawl_client.scrape(
            page_url, want_links=True, want_html=True, actions=actions
        )

        if not (set(links) - seen_links):
            break  # pressing further surfaced nothing new - exhausted, whatever the probe claims
        seen_links |= set(links)

        probe = llm_extractor.detect_load_more(page_url, html)

    # Only now, on the fully-loaded (or round-capped) page, actually read events.
    candidates = filter_candidate_links(links, homepage_url)
    analysis = llm_extractor.analyze_listing_page(page_url, markdown, candidates)
    return analysis["event_urls"], candidates, analysis["next_page_url"]


def _crawl_one_listing_url(
    session: Session, organiser: Organiser, listing_url: str, seen: set[str]
) -> list[str]:
    """
    Crawl one listing URL, following numbered pagination (a distinct
    next_page_url each time) up to _MAX_LISTING_PAGES. Each page visited gets
    its own CrawlRun audit row. Returns the new event URLs found across all
    pages of this listing.
    """
    new_urls: list[str] = []
    visited: set[str] = set()
    page_url: str | None = listing_url

    for _ in range(_MAX_LISTING_PAGES):
        if page_url is None or page_url in visited:
            break
        visited.add(page_url)

        run = CrawlRun(
            run_type=CrawlRunType.LISTING,
            target_url=page_url,
            organiser_id=organiser.id,
            status=CrawlStatus.FAILED,
            started_at=datetime.now(timezone.utc),
        )

        if not robots.is_allowed(page_url):
            run.status = CrawlStatus.SKIPPED
            run.detail = "disallowed by robots.txt"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            break

        try:
            event_links, candidates, next_page_url = _analyze_page(page_url, organiser.homepage_url)

            existing_urls = set(
                session.scalars(select(Event.url).where(Event.url.in_(event_links))).all()
            )
            page_new = [u for u in event_links if u not in existing_urls and u not in seen]
            seen.update(page_new)
            new_urls.extend(page_new)

            run.status = CrawlStatus.SUCCESS
            run.detail = f"{len(candidates)} candidate links, {len(event_links)} confirmed events, {len(page_new)} new"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)

            page_url = next_page_url
        except requests.exceptions.ConnectionError:
            raise  # can't reach Firecrawl at all - stop the run rather than retrying every remaining listing
        except Exception as e:
            run.detail = f"{type(e).__name__}: {e}"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            break

    return new_urls


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
        new_urls.extend(_crawl_one_listing_url(session, organiser, listing_url, seen))

    return new_urls
