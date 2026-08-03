"""
Stage 2 of the pipeline: fetch one event detail page, extract structured
fields via LLM, and upsert into the events table.

Skips the LLM call entirely when a re-crawl finds the page unchanged
(content_hash match) — this is what keeps recurring re-crawls of the same
organiser cheap.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services import firecrawl_client, llm_extractor, robots
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event


def _hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def preview_event(event_url: str) -> dict | None:
    """
    Dry-run counterpart to crawl_event: fetches the page and runs extraction,
    but never touches the database (no Event upsert, no CrawlRun). Prints the
    extracted fields and returns them.
    """
    if not robots.is_allowed(event_url):
        print(f"  [dry-run] {event_url}: disallowed by robots.txt")
        return None

    try:
        markdown, _links = firecrawl_client.scrape(event_url, want_links=False)
        fields = llm_extractor.extract_event_fields(event_url, markdown)
    except Exception as e:
        print(f"  [dry-run] {event_url}: FAILED ({type(e).__name__}: {e})")
        return None

    if fields is None:
        print(f"  [dry-run] {event_url}: extraction returned no fields")
        return None

    print(f"  [dry-run] {event_url}:")
    for key, value in fields.items():
        print(f"    {key}: {value}")
    return fields


def crawl_event(session: Session, organiser_id: int, event_url: str) -> Event | None:
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

    try:
        markdown, _links = firecrawl_client.scrape(event_url, want_links=False)
        content_hash = _hash(markdown)

        existing = session.scalar(select(Event).where(Event.url == event_url))

        if existing and existing.content_hash == content_hash:
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
        event.distance_text = fields.get("distance_text")
        event.price_text = fields.get("price_text")
        event.age_restriction_text = fields.get("age_restriction_text")
        event.raw_markdown = markdown
        event.content_hash = content_hash
        event.last_seen_at = now
        event.last_crawled_at = now

        run.status = CrawlStatus.SUCCESS
        run.detail = "extracted"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return event
    except Exception as e:
        run.detail = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return None
