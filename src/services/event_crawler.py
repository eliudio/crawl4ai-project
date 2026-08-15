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

from services import firecrawl_client, llm_extractor, robots
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event, EventDistance


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
        markdown, _links, _html, _url = firecrawl_client.scrape(event_url, want_links=False)
        content_hash = _hash(markdown)

        if check_mode == "hash-check" and existing and existing.content_hash == content_hash:
            existing.last_seen_at = now
            existing.last_crawled_at = now
            run.status = CrawlStatus.SUCCESS
            run.detail = "unchanged, skipped extraction"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            return existing

        fields = llm_extractor.extract_event_fields(event_url, markdown)
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

        # Re-extraction (a changed page) replaces the whole set rather than trying to
        # match old vs. new entries one-to-one - distance wording can change between
        # crawls, so there's no stable key to update in place. cascade="all,
        # delete-orphan" on Event.distances takes care of removing the old rows.
        event.distances.clear()
        for i, d in enumerate(fields.get("distances") or []):
            event.distances.append(
                EventDistance(distance_text=d["distance_text"], price_text=d.get("price_text"), sort_order=i)
            )
        event.raw_markdown = markdown
        event.content_hash = content_hash
        event.last_seen_at = now
        event.last_crawled_at = now

        run.status = CrawlStatus.SUCCESS
        run.detail = "extracted"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return event
    except requests.exceptions.ConnectionError:
        raise  # can't reach Firecrawl at all - stop the run rather than retrying every remaining URL
    except Exception as e:
        run.detail = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return None
