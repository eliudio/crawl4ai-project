"""
Turns an already-resolved fields dict (see llm/event_extraction.extract_event_fields's
return shape) into Event/EventDistance/EventOccurrence rows. Shared by both pipelines
that produce event data - pattern_site/event_crawler.crawl_event's own real-LLM-
extraction path, and register_event_from_fields below (the structured-bulk-feed
importers' registry, e.g. feeds/parkrun_import.py's run_import) - so both stay in
lockstep on every field this schema grows, not just whichever one happened to be
updated for a given feature.

Living here (not in pattern_site/) is deliberate: this module has no idea whether the
fields it's given came from actually scraping a page or from a feed that already had
them, and neither pipeline should have to reach into the other's package to share it.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.models import (
    CrawlRun,
    CrawlRunType,
    CrawlStatus,
    Event,
    EventDistance,
    EventLifecycle,
    EventOccurrence,
    EventStatus,
    Occurrence,
    RegistrationStatus,
)
from services.llm import rewrite_summary

from .geocoding_client import geocode_event_location
from .race_types import get_or_create_race_type

__all__ = ["apply_fields", "register_event_from_fields"]


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


def apply_fields(session: Session, event: Event, fields: dict, registrator: str) -> None:
    """
    Writes an extract_event_fields()-shaped dict onto `event` (and its distances/
    occurrences) - shared by pattern_site/event_crawler.crawl_event's own real-LLM-
    extraction path and register_event_from_fields's direct-from-source-data path
    below.

    Two fields support a caller-supplied override instead of the normal LLM-only
    derivation, used by register_event_from_fields (parkrun's own feed already HAS
    these, more reliably than re-deriving them):
    - summary_alt/summary_short: if the fields dict already supplies both, used as-is,
      skipping the rewrite_summary LLM call entirely (llm.extract_event_fields's own
      return value never includes these two keys at all, so the real extraction path
      is unaffected - "not in fields" there, not "falsy").
    - latitude/longitude: if the fields dict already supplies both (non-null), used
      directly, skipping the geocode_event_location call entirely - same "key
      genuinely absent for the real extraction path" reasoning.
    """
    event.name = fields.get("name")
    event.summary = fields.get("summary")
    if "summary_alt" in fields and "summary_short" in fields:
        event.summary_alt = fields.get("summary_alt")
        event.summary_short = fields.get("summary_short")
    elif event.summary:
        # See llm.rewrite_summary - a second, cheap LLM call producing an alternative,
        # genuinely reworded version (summary_alt) plus a further-condensed one-sentence
        # version of it (summary_short). Kept alongside the original rather than
        # replacing it - see admin/export, which shows all three. Only called when
        # there's actually a summary to work with - nothing to rewrite or condense
        # otherwise (e.g. an invalid/no-content page never gets one).
        rewritten = rewrite_summary(event.summary)
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
    # See RegistrationStatus/llm.event_extraction's own registration_status docs.
    # _normalize_registration_status in llm/event_extraction.py already guarantees a
    # valid enum value, same pattern as occurrence below.
    event.registration_status = RegistrationStatus(
        fields.get("registration_status") or RegistrationStatus.UNKNOWN.value
    )
    event.registration_text = fields.get("registration_text")
    registration_opens_date = _parse_iso_date(fields.get("registration_opens_date_iso"))
    event.registration_opens_at = (
        datetime.combine(
            registration_opens_date,
            _parse_24h_time(fields.get("registration_opens_time_24h")) or time(0, 0),
            tzinfo=timezone.utc,
        )
        if registration_opens_date
        else None
    )
    registration_closes_date = _parse_iso_date(fields.get("registration_closes_date_iso"))
    event.registration_closes_at = (
        datetime.combine(
            registration_closes_date,
            _parse_24h_time(fields.get("registration_closes_time_24h")) or time(0, 0),
            tzinfo=timezone.utc,
        )
        if registration_closes_date
        else None
    )
    # See EventLifecycle/llm.event_extraction's own lifecycle_status docs. Independent
    # of registration_status above - a cancelled event doesn't imply anything about
    # whether registration was open/closed, and vice versa.
    event.lifecycle_status = EventLifecycle(
        fields.get("lifecycle_status") or EventLifecycle.SCHEDULED.value
    )
    event.lifecycle_text = fields.get("lifecycle_text")
    # See EventStatus/llm.event_extraction's is_valid_event - a page that's just a
    # redirect notice, dead page, etc. is kept (not discarded) so it's visible for
    # review, but flagged rather than treated as a normal event with unusually empty
    # fields.
    event.status = EventStatus.VALID if fields.get("is_valid_event", True) else EventStatus.INVALID
    event.invalid_reason = fields.get("invalid_reason")

    # Re-extraction (a changed page, or a re-registration from a re-fetched feed)
    # replaces the whole set rather than trying to match old vs. new entries one-to-
    # one - distance wording can change between crawls, so there's no stable key to
    # update in place. cascade="all, delete-orphan" on Event.distances takes care of
    # removing the old rows.
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
                registrator=registrator,
            )
        )
    # See common/models's Occurrence docstring for the two mechanisms this splits
    # into. _normalize_occurrence in llm/event_extraction.py already guarantees a
    # valid enum value.
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
            continue  # unparseable despite passing llm/event_extraction's own required-field check
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
                registrator=registrator,
            )
        )

    latitude, longitude = fields.get("latitude"), fields.get("longitude")
    if latitude is not None and longitude is not None:
        event.latitude, event.longitude = latitude, longitude
    else:
        # Geocoded once here, cached on the row - never looked up at query time (see
        # geocoding_client.py). Only when at least one location field is present; a
        # geocoding hiccup (or nothing to geocode at all) leaves lat/lon as they were
        # rather than clearing a previously-successful geocode from an earlier crawl.
        geocoded = geocode_event_location(event.location, event.start_location, event.finish_location)
        if geocoded is not None:
            event.latitude, event.longitude = geocoded


def register_event_from_fields(
    session: Session, organiser_id: int, event_url: str, fields: dict, registrator: str
) -> Event | None:
    """
    Upserts an Event (+ distances/occurrence) directly from an already-fully-resolved
    fields dict - no robots.txt check, no scrape, no LLM call at all, unlike
    pattern_site/event_crawler.crawl_event. Used by the structured-bulk-feed
    importers in feeds/feed_importers.py's registry (e.g. feeds/parkrun_import.py's
    run_import) - a source whose own feed already supplies everything needed to build
    a real Event (name, location, exact coordinates, and - via that importer's own
    build_event_fields - which of the standing weekly schedules applies, for a source
    like parkrun), so there's nothing left for a per-page scrape/LLM call to add. For
    parkrun specifically, confirmed in practice: the event page itself tends to come
    back HTTP 403 (parkrun's own anti-bot protection) when actually scraped anyway -
    so scraping it at all would be pure downside even setting the above aside.

    No robots.txt check here: unlike crawl_event's own event-page fetch, this never
    makes an outbound request to the event's own URL at all - the only real request
    (the calling importer's own feed/TSV fetch) already went through its own
    robots.txt-respecting-or-not decision before this function is ever called.
    """
    now = datetime.now(timezone.utc)
    run = CrawlRun(
        run_type=CrawlRunType.EVENT,
        target_url=event_url,
        organiser_id=organiser_id,
        status=CrawlStatus.FAILED,
        started_at=now,
    )

    try:
        existing = session.scalar(select(Event).where(Event.url == event_url))
        if existing:
            event = existing
        else:
            event = Event(organiser_id=organiser_id, url=event_url, first_seen_at=now)
            session.add(event)
        event.registrator = registrator

        apply_fields(session, event, fields, registrator)

        event.last_seen_at = now
        event.last_crawled_at = now

        run.status = CrawlStatus.SUCCESS
        run.detail = "registered directly from feed data (no scrape)"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return event
    except Exception as e:
        # Same reasoning as crawl_event's own except-block - a mid-build DB error must
        # not leave the session's transaction unusable for whatever runs next on it.
        session.rollback()
        run.detail = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        return None
