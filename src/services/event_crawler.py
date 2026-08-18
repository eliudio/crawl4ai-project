"""
Stage 2 of the pipeline: fetch one event detail page, extract structured
fields via LLM, and upsert into the events table.

Skips the LLM call entirely when a re-crawl finds the page unchanged
(content_hash match) — this is what keeps recurring re-crawls of the same
organiser cheap.
"""

import hashlib
from datetime import date, datetime, time, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from services import geocoding_client, llm_extractor, robots, scraper_client, structured_data
from services.config import settings
from services.models import (
    CrawlRun,
    CrawlRunType,
    CrawlStatus,
    Event,
    EventDistance,
    EventOccurrence,
    EventStatus,
    Occurrence,
)
from services.race_types import get_or_create_race_type


def _hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _parse_iso_date(value: str | None) -> date | None:
    """Best-effort only - a malformed/unparseable ISO date must not fail the whole
    crawl, just leave the field null (same spirit as every other extraction step here)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_24h_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


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
    inserted as new - see local_runner.py's --force-refresh, for re-running a
    whole organiser's events after a fix to the extraction pipeline itself,
    where a stale content_hash match would otherwise skip every event that
    still needs picking up the corrected fields.
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

    if not robots.is_allowed(event_url):
        # This is what a caller's own "ok"/"FAILED" print (see local_runner.py,
        # main.py) can't tell apart on its own - crawl_event returning None here
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
            run.status = CrawlStatus.SUCCESS
            run.detail = f"confirmed dead link (HTTP {status_code})"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            return event

        # want_html=True even though this is otherwise a markdown-only fetch - needed for
        # structured_data.extract_event_fields to read the page's own schema.org JSON-LD
        # (see below), which markdown conversion strips out along with every other <script>.
        markdown, _links, html, _url = scraper_client.scrape(event_url, want_links=False, want_html=True)
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
        # states (see structured_data.py) before ever calling the LLM, so it's only asked to
        # fill in whatever wasn't already there (llm_extractor.extract_event_fields removes
        # these keys from its own schema/required list entirely, not just offered as a hint).
        known_fields = structured_data.extract_event_fields(html)
        if known_fields:
            print(f"{datetime.now():%H:%M:%S} - structured data (JSON-LD) supplied: {sorted(known_fields)}")

        fields = llm_extractor.extract_event_fields(event_url, markdown, known_fields=known_fields)
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

        event.name = fields.get("name")
        event.summary = fields.get("summary")
        # See llm_extractor.rewrite_summary - a second, cheap LLM call producing an
        # alternative, genuinely reworded version (summary_alt) plus a further-condensed
        # one-sentence version of it (summary_short). Kept alongside the original rather
        # than replacing it - see export_events.py, which now shows all three. Only called
        # when there's actually a summary to work with - nothing to rewrite or condense
        # otherwise (e.g. an invalid/no-content page never gets one).
        if event.summary:
            rewritten = llm_extractor.rewrite_summary(event.summary)
            event.summary_alt = rewritten["summary_alt"]
            event.summary_short = rewritten["summary_short"]
        else:
            event.summary_alt = None
            event.summary_short = None
        event.sport = fields.get("sport")
        event.date_text = fields.get("date_text")
        event.location = fields.get("location")
        event.start_location = fields.get("start_location")
        event.finish_location = fields.get("finish_location")
        event.age_restriction_text = fields.get("age_restriction_text")
        # See EventStatus/llm_extractor's is_valid_event - a page that's just a redirect
        # notice, dead page, etc. is kept (not discarded) so it's visible for review, but
        # flagged rather than treated as a normal event with unusually empty fields.
        event.status = EventStatus.VALID if fields.get("is_valid_event", True) else EventStatus.INVALID
        event.invalid_reason = fields.get("invalid_reason")

        # Re-extraction (a changed page) replaces the whole set rather than trying to
        # match old vs. new entries one-to-one - distance wording can change between
        # crawls, so there's no stable key to update in place. cascade="all,
        # delete-orphan" on Event.distances takes care of removing the old rows.
        event.distances.clear()
        for i, d in enumerate(fields.get("distances") or []):
            # Resolves (or creates, first time this exact combination is seen) the
            # shared RaceType row for this distance - see race_types.py. None when
            # distance_category couldn't be determined; the raw distance_text/
            # price_text are still stored on the EventDistance either way.
            race_type = get_or_create_race_type(session, event.sport, d.get("distance_category"))
            event.distances.append(
                EventDistance(
                    distance_text=d["distance_text"],
                    price_text=d.get("price_text"),
                    sort_order=i,
                    race_type=race_type,
                )
            )
        # See models.py's Occurrence docstring for the two mechanisms this splits into.
        # _normalize_occurrence in llm_extractor.py already guarantees a valid enum value.
        event.occurrence = Occurrence(fields.get("occurrence") or Occurrence.ONE_OFF.value)
        event.occurrence_weekdays = fields.get("occurrence_weekdays") or None
        event.occurrence_time = _parse_24h_time(fields.get("occurrence_time"))
        event.occurrence_starts_on = _parse_iso_date(fields.get("occurrence_starts_on"))
        event.occurrence_ends_on = _parse_iso_date(fields.get("occurrence_ends_on"))

        # Same re-extraction reasoning as distances above: replace the whole set rather
        # than match old vs. new one-to-one - see EventOccurrence's own docstring for why
        # a platform-native external_ticket_id (when captured) is a better re-crawl key
        # than this delete-and-reinsert approach, not yet wired in as a source field here.
        event.occurrences.clear()
        for i, o in enumerate(fields.get("occurrences") or []):
            occurrence_date = _parse_iso_date(o.get("date_iso"))
            if occurrence_date is None:
                continue  # unparseable despite passing llm_extractor's own required-field check
            starts_at = datetime.combine(
                occurrence_date, _parse_24h_time(o.get("time_24h")) or time(0, 0), tzinfo=timezone.utc
            )
            event.occurrences.append(
                EventOccurrence(
                    starts_at=starts_at,
                    date_text=o.get("date_text"),
                    time_text=o.get("time_text"),
                    price_text=o.get("price_text"),
                    sort_order=i,
                )
            )

        # Geocoded once here, cached on the row - never looked up at query time (see
        # geocoding_client.py). Only when at least one location field is present; a
        # geocoding hiccup (or nothing to geocode at all) leaves lat/lon as they were
        # rather than clearing a previously-successful geocode from an earlier crawl.
        geocoded = geocoding_client.geocode_event_location(
            event.location, event.start_location, event.finish_location
        )
        if geocoded is not None:
            event.latitude, event.longitude = geocoded

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
