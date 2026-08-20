"""
Stage 2 of the pattern-website pipeline: fetch one event detail page, extract
structured fields via LLM, and upsert into the events table - see
events/registration.py for the actual "fields dict -> Event row" writing
logic, shared with the structured-bulk-feed pipeline (feeds/parkrun_import.py).

Skips the LLM call entirely when a re-crawl finds the page unchanged
(content_hash match) — this is what keeps recurring re-crawls of the same
organiser cheap.
"""

import hashlib
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.config import settings
from services.common.models import CrawlRun, CrawlRunType, CrawlStatus, Event, EventStatus, Organiser
from services.events import apply_fields
from services.llm import extract_event_fields
from services.scraping import extract_event_fields as extract_structured_data_fields
from services.scraping import is_allowed, scrape

__all__ = ["crawl_event"]


def _hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


# Confirmed dead - the page itself says so, not just "the scraper/LLM couldn't make
# sense of it" (which could just as easily be a transient anti-bot block or a rendering
# glitch, worth retrying next run). 410 Gone is the explicit "never coming back" cousin
# of 404.
_DEAD_LINK_STATUS_CODES = {404, 410}

_CHECK_MODES = {"hash-check", "url-check", "force"}


def _dead_link_status(url: str) -> int | None:
    """
    Cheap upfront check for a URL that's definitively gone, before ever spending a
    browser scrape + LLM call on it. Plain requests.head/get straight to the site (not
    through crawl4ai/Firecrawl) - what actually caught this in practice was a listing
    page linking to an event whose real URL now 404s (confirmed with
    urllib.request.urlopen), which crawl4ai still "successfully" rendered as a
    near-empty error page, so extract_event_fields kept returning None and the URL,
    never having become an Event row, kept being reported as "new" and retried every
    single run.

    Returns the HTTP status code if the request could be made at all, None if it
    couldn't (timeout, connection refused, blocked by anti-bot, ...) - those are
    inconclusive, not evidence the page is dead, so callers must fall through to the
    normal scrape attempt rather than treat None as "confirmed alive".
    """
    headers = {"User-Agent": settings.user_agent}
    try:
        response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code == 405:  # some servers reject HEAD outright
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True, stream=True)
        return response.status_code
    except requests.exceptions.RequestException:
        return None


def crawl_event(
    session: Session, organiser_id: int, event_url: str, check_mode: str = "hash-check"
) -> Event | None:
    """
    check_mode "hash-check" (default): always fetch the page, but skip
    re-extraction if its content hash matches what's stored - catches changed
    events, not just new ones.
    check_mode "url-check": skip entirely (no fetch) if the URL is already in
    the database, regardless of whether the page changed since.
    check_mode "force": always fetch and always re-extract, even if the URL is
    already stored and its content hasn't changed - neither the url-check nor
    hash-check skip below ever applies. An existing row is updated in place
    (same as a normal hash-check re-extraction), a URL with no existing row is
    inserted as new - see local/local_event_scraper.py's --force-refresh, for
    re-running a whole organiser's events after a fix to the extraction
    pipeline itself, where a stale content_hash match would otherwise skip
    every event that still needs picking up the corrected fields.
    """
    if check_mode not in _CHECK_MODES:
        raise ValueError(f"unknown check_mode: {check_mode!r} (expected one of {sorted(_CHECK_MODES)})")

    now = datetime.now(timezone.utc)
    run = CrawlRun(
        run_type=CrawlRunType.EVENT,
        target_url=event_url,
        organiser_id=organiser_id,
        status=CrawlStatus.FAILED,
        started_at=now,
    )

    # See Organiser.registrator's own docstring. A column-only select (not
    # session.get(Organiser, ...)) deliberately - Organiser.listing_urls is a
    # Postgres-only ARRAY column the whole test suite already can't build on SQLite
    # (see test_db.py/test_race_types.py/etc.'s own comments to that effect); selecting
    # only .registrator never touches it, so this still works against the lightweight
    # SQLite schemas this module's own tests use. "bot" (robots.txt fully respected) is
    # the correct fallback for a missing organiser - never a real gap in practice, every
    # organiser_id crawled here comes from a real row, but never silently default to
    # something more permissive just because the lookup came back empty.
    registrator = session.scalar(select(Organiser.registrator).where(Organiser.id == organiser_id)) or "bot"

    if not is_allowed(event_url, registrator=registrator):
        # This is what a caller's own "ok"/"FAILED" print (see local/local_event_scraper.py,
        # server/main.py) can't tell apart on its own - crawl_event returning None here
        # looks identical to a genuine scrape/extraction failure from the outside.
        # ROBOTS-SKIP is the one grep-able marker every skip site in this codebase
        # uses (see also listing_crawler.py, sitemap_crawler.py) so this case is
        # never mistaken for a real failure while reading/searching the log.
        print(f"ROBOTS-SKIP: {event_url} (event)")
        run.status = CrawlStatus.SKIPPED
        run.detail = "disallowed by robots.txt"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return None

    existing = session.scalar(select(Event).where(Event.url == event_url))

    if check_mode == "url-check" and existing:
        existing.last_seen_at = now
        run.status = CrawlStatus.SKIPPED
        run.detail = "url already exists, skipped (url-check)"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return existing

    try:
        status_code = _dead_link_status(event_url)
        if status_code in _DEAD_LINK_STATUS_CODES:
            if existing:
                event = existing
            else:
                event = Event(organiser_id=organiser_id, url=event_url, first_seen_at=now)
                session.add(event)
            event.status = EventStatus.INVALID
            event.invalid_reason = f"HTTP {status_code} fetching the page"
            event.last_seen_at = now
            event.last_crawled_at = now
            # Refreshed on every crawl (not just set at creation) - see Event.registrator's
            # own docstring: always reflects who/what is CURRENTLY responsible, not whoever
            # happened to first discover this URL.
            event.registrator = registrator
            run.status = CrawlStatus.SUCCESS
            run.detail = f"confirmed dead link (HTTP {status_code})"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            return event

        # want_html=True even though this is otherwise a markdown-only fetch - needed for
        # scraping.extract_event_fields to read the page's own schema.org JSON-LD (see
        # below), which markdown conversion strips out along with every other <script>.
        markdown, _links, html, _url = scrape(event_url, want_links=False, want_html=True)
        content_hash = _hash(markdown)

        if check_mode == "hash-check" and existing and existing.content_hash == content_hash:
            existing.last_seen_at = now
            existing.last_crawled_at = now
            run.status = CrawlStatus.SUCCESS
            run.detail = "unchanged, skipped extraction"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            return existing

        # Deterministic and free - read whatever this page's own schema.org JSON-LD already
        # states (see scraping/structured_data.py) before ever calling the LLM, so it's only
        # asked to fill in whatever wasn't already there (llm.extract_event_fields removes
        # these keys from its own schema/required list entirely, not just offered as a hint).
        known_fields = extract_structured_data_fields(html)
        if known_fields:
            print(f"{datetime.now():%H:%M:%S} - structured data (JSON-LD) supplied: {sorted(known_fields)}")

        fields = extract_event_fields(event_url, markdown, known_fields=known_fields)
        if fields is None:
            run.detail = "extraction returned no fields"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            return None

        if existing:
            event = existing
        else:
            event = Event(organiser_id=organiser_id, url=event_url, first_seen_at=now)
            session.add(event)
        # See the dead-link branch above's own comment - refreshed every crawl.
        event.registrator = registrator

        apply_fields(session, event, fields, registrator)

        event.raw_markdown = markdown
        event.content_hash = content_hash
        event.last_seen_at = now
        event.last_crawled_at = now

        run.status = CrawlStatus.SUCCESS
        run.detail = "extracted" if event.status == EventStatus.VALID else f"extracted (invalid event: {event.invalid_reason})"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return event
    except requests.exceptions.ConnectionError:
        raise  # can't reach Firecrawl at all - stop the run rather than retrying every remaining URL
    except Exception as e:
        # Anything that failed here (e.g. a DB constraint/length error surfacing via
        # autoflush of the pending `event` while querying, such as
        # get_or_create_race_type) leaves the session's transaction unusable until
        # it's rolled back - without this, the *next* statement on this session (even
        # session_scope's own closing commit) raises PendingRollbackError instead of
        # the original error, which looks like an unrelated crash and takes the whole
        # batch run down with it instead of just this one event.
        session.rollback()
        run.detail = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return None
