"""
Stage 2 of the pipeline: fetch one event detail page, extract structured
fields via LLM, and upsert into the events table.

Skips the LLM call entirely when a re-crawl finds the page unchanged
(content_hash match) — this is what keeps recurring re-crawls of the same
organiser cheap.
"""

import hashlib
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from services import llm_extractor, robots, scraper_client, structured_data
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event, EventDistance, EventStatus
from services.race_types import get_or_create_race_type


def _hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def crawl_event(
    session: Session, organiser_id: int, event_url: str, check_mode: str = "hash-check"
) -> Event | None:
    """
    check_mode "hash-check" (default): always fetch the page, but skip
    re-extraction if its content hash matches what's stored - catches changed
    events, not just new ones.
    check_mode "url-check": skip entirely (no fetch) if the URL is already in
    the database, regardless of whether the page changed since.
    """
    now = datetime.now(timezone.utc)
    run = CrawlRun(
        run_type=CrawlRunType.EVENT,
        target_url=event_url,
        organiser_id=organiser_id,
        status=CrawlStatus.FAILED,
        started_at=now,
    )

    if not robots.is_allowed(event_url):
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
