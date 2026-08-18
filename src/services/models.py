"""
ORM models for the events pipeline (Cloud SQL / Postgres).

Table design notes:
- `Organiser.source_type` is the enforcement point for the "never store
  aggregator/platform data" rule: only rows with source_type == "organiser"
  are ever enqueued for event crawling (see listing_crawler.py). Aggregators
  are only ever a *source of organiser URLs* (phase 2), never a source of
  event rows.
- `Event.content_hash` lets a re-crawl skip re-extraction (and therefore the
  LLM call) when a page hasn't changed since last time.
"""

from datetime import date, datetime, time, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceType(PyEnum):
    ORGANISER = "organiser"
    AGGREGATOR = "aggregator"
    PLATFORM = "platform"


class CrawlRunType(PyEnum):
    LISTING = "listing"
    EVENT = "event"


class CrawlStatus(PyEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventStatus(PyEnum):
    """
    VALID: the page actually describes a specific event.
    INVALID: the crawled URL doesn't - e.g. it's just a redirect notice to
    another site (confirmed in practice: runthrough.co.uk/event/running-tours-
    copenhagen-marathon is literally just "We are redirecting you to
    runnerretreats.com"), a dead/error page, or otherwise has no real event
    content to extract. Distinct from the crawl failing outright (that's
    CrawlStatus.FAILED / a None return from crawl_event) - this is a *successful*
    crawl of a page that turns out not to be an event page at all, so it's worth
    keeping the row (rather than silently discarding it) with the reason why.
    """

    VALID = "valid"
    INVALID = "invalid"


class Occurrence(PyEnum):
    """
    How an event recurs. Two genuinely different storage mechanisms sit behind
    this, decided by whether the organiser's own page enumerates concrete dates
    at all - see Event's own occurrence_* columns and EventOccurrence below:

    - ONE_OFF / SPECIFIC_DATES: bounded - a finite, known set of dates (a
      single date, or several individually listed/ticketed ones - e.g.
      atwevents.co.uk's own per-session tickets, one row each). These live in
      EventOccurrence, one row per known date+time. A one-off event is simply
      a SPECIFIC_DATES-shaped event with exactly one row - ONE_OFF exists as
      its own value purely as a descriptive label for humans/UI, not a
      different storage path.
    - DAILY / WEEKLY / MONTHLY / YEARLY: unbounded - a standing rule with no
      enumerable dates at all (e.g. parkrun: "every Saturday, 9am", forever,
      no page ever lists individual future dates). Represented by Event's own
      occurrence_weekdays/occurrence_time/occurrence_starts_on/
      occurrence_ends_on directly - EventOccurrence stays empty for these,
      that's correct, not a gap.
    """

    ONE_OFF = "one_off"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    SPECIFIC_DATES = "specific_dates"


class Sport(PyEnum):
    """
    Fixed, closed vocabulary for RaceType.sport - unlike Event.sport (free text,
    straight from the LLM, since forcing an exact enum match there risks losing an
    event entirely over a wording mismatch like "athletics"), this is deliberately
    strict: RaceType rows are a shared lookup table other events reference, so its
    own sport needs to be one of a small fixed set for that to mean anything.
    race_types.get_or_create_race_type() coerces Event.sport's free text into this
    enum (falling back to OTHER), rather than this being fed by the LLM directly.
    """

    RUNNING = "running"
    CYCLING = "cycling"
    SWIMMING = "swimming"
    TRIATHLON = "triathlon"
    MULTI_SPORT = "multi_sport"
    WALKING = "walking"
    OBSTACLE = "obstacle"
    OTHER = "other"


class Organiser(Base):
    __tablename__ = "organisers"
    __table_args__ = (UniqueConstraint("homepage_url", name="uq_organiser_homepage_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    homepage_url: Mapped[str] = mapped_column(String(1024))
    # Several organisers split listings across multiple sub-pages (e.g. by
    # category), so this is a list rather than a single URL. Empty/absent
    # until discover_listing_urls() (see llm_extractor.py) has run once.
    listing_urls: Mapped[list[str]] = mapped_column(ARRAY(String(1024)), default=list)

    # Which listing-discovery mechanism this organiser uses - looked up in
    # discovery_handlers.py's registry by listing_crawler.py's crawl_listing(), the
    # one place that dispatches on it. "default" (the vast majority of organisers)
    # covers everything listing_crawler.py always did: prefer a sitemap when
    # handler_params has one, otherwise guess via LLM/pagination/"load more". Every
    # organiser has exactly one handler, never null - there's no separate "normal vs
    # custom" tier; "default" is just as much a named, registered handler as
    # "parkrun" is, not an implicit fallback. server_default (not just the Python-
    # side default= below) so this NOT NULL column can still be added to an
    # already-existing `organisers` table by db.py's _add_missing_columns - see
    # Event.occurrence's own comment for why a plain default= alone isn't enough.
    handler: Mapped[str] = mapped_column(String(64), default="default", server_default="default")
    # Optional, handler-specific config - e.g. the "default" handler's own sitemap
    # URL (see tools/seed_organisers.py: kept as its own flat "sitemap_url" column in
    # the seed CSV itself, specifically so discover_sitemaps.py - a plain
    # csv.DictWriter script - never has to parse/merge JSON just to set one string;
    # only merged into this dict at seed time), or "parkrun"'s own country_code.
    # Each handler is responsible for reading whatever keys it expects out of this,
    # with its own sensible defaults - deliberately not a per-handler schema, since
    # this is expected to stay a small, hand-maintained set of special cases.
    handler_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Only "organiser" rows are ever fed to the event-crawl queue. Rows
    # discovered on aggregator/platform sites are recorded for provenance
    # but structurally excluded from event crawling.
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), default=SourceType.ORGANISER
    )
    discovered_via: Mapped[str | None] = mapped_column(String(255), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    events: Mapped[list["Event"]] = relationship(back_populates="organiser")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("url", name="uq_event_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organiser_id: Mapped[int] = mapped_column(ForeignKey("organisers.id"))
    url: Mapped[str] = mapped_column(String(1024))

    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Original summary, as extracted - either LLM-rephrased from the page's own markdown,
    # or (see structured_data.py) read verbatim from the page's own schema.org JSON-LD
    # description when present.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-rewritten alternative wording of `summary` above (see llm_extractor.rewrite_summary) -
    # genuinely reworded, not a close paraphrase, so what gets stored/republished (e.g.
    # export_events.py's HTML export) never has to be another site's own copy verbatim.
    summary_alt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-condensed single-sentence summary of `summary` above (see llm_extractor.rewrite_summary).
    summary_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    sport: Mapped[str | None] = mapped_column(String(64), nullable=True)  # running, cycling, ...
    date_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    start_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finish_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Text, not String(N) - unlike location/name, this is a free-form rule/paragraph
    # straight from the LLM with no realistic length assumption (e.g. Frimley Health
    # Charity's "Run Frimley 2022" page has a 3-clause, 270+ char version of this).
    age_restriction_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # See EventStatus - defaults to VALID since most crawled URLs really are events;
    # event_crawler.py sets this from the LLM's own is_valid_event/invalid_reason
    # verdict (see llm_extractor.py) rather than us guessing from empty fields.
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"), default=EventStatus.VALID
    )
    invalid_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # See Occurrence's own docstring for the two mechanisms this splits into.
    # Defaults to ONE_OFF, same reasoning as EventStatus defaulting to VALID -
    # most crawled events really are one-off.
    # server_default (not just the Python-side default= above) so this NOT NULL column
    # can still be added to an already-existing `events` table by db.py's
    # _add_missing_columns - a plain default= only applies to rows the ORM itself
    # inserts, never to backfilling existing rows via ALTER TABLE ADD COLUMN.
    occurrence: Mapped[Occurrence] = mapped_column(
        # values_callable: without it, SQLAlchemy stores each member's `.name`
        # (ONE_OFF, DAILY, ...) as the Postgres enum's labels, not its `.value`
        # ("one_off", "daily", ...) - which is what server_default below (and
        # everywhere else this enum's string form is used - llm_extractor's
        # schema, event_crawler's Occurrence(fields.get("occurrence"))) treats
        # as canonical. Left mismatched, CREATE TABLE's own DEFAULT 'one_off'
        # fails validation against the type it just created from names.
        Enum(Occurrence, name="event_occurrence", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        default=Occurrence.ONE_OFF,
        server_default=Occurrence.ONE_OFF.value,
    )
    # Only meaningful for DAILY/WEEKLY/MONTHLY/YEARLY (an unbounded recurrence with
    # nothing to enumerate) - which weekday(s) it falls on, lowercase 3-letter
    # abbreviations ("mon"/"tue"/"wed"/"thu"/"fri"/"sat"/"sun"), e.g. parkrun -> ["sat"].
    # A list rather than one value, since a single rule can cover more than one weekday
    # (e.g. a club's "Tue/Thu evening" sessions). Null for ONE_OFF/SPECIFIC_DATES, where
    # EventOccurrence rows carry the real dates instead.
    # JSON, not Postgres' ARRAY (unlike Organiser.listing_urls) - `events` gets its real
    # table created directly on SQLite throughout the test suite (test_event_crawler.py,
    # test_db.py, ...), which ARRAY can't compile for; JSON round-trips a list of strings
    # fine on both backends.
    occurrence_weekdays: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    occurrence_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # Validity window for an unbounded recurrence - both NULL means indefinite
    # (parkrun's actual "forever"); both set means a seasonal pattern (e.g.
    # atwevents.co.uk's swim lake, "Easter until end-September") - without this, a
    # plain weekday match would report a seasonal event as happening year-round.
    occurrence_starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    occurrence_ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Geocoded from start_location (falling back to location, then finish_location -
    # same priority export_events.py's _render_map already uses) via
    # geocoding_client.py, once per crawl - never looked up at query time. Null until
    # a crawl has actually attempted geocoding (or attempted and found nothing) -
    # there's no third "not yet tried" state distinct from "tried, no result", since
    # a failed/empty geocode isn't retried differently from never having tried at all.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organiser: Mapped["Organiser"] = relationship(back_populates="events")
    # Most events offer more than one distance, each with its own price (e.g. "5k: £15",
    # "10k: £20") - that's a one-to-many relationship, not a pair of scalar columns on
    # Event, so it gets its own table. order_by preserves the order distances were listed
    # on the page (sort_order), and delete-orphan means re-extraction (event_crawler.py
    # clears event.distances before re-appending) doesn't leave stale rows behind.
    distances: Mapped[list["EventDistance"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventDistance.sort_order"
    )
    # Concrete known dates - see Occurrence's own docstring: populated for a one-off
    # event (exactly one row) or a bounded/enumerated recurring event (one row per
    # listed date), left empty for an unbounded recurrence (nothing to enumerate).
    occurrences: Mapped[list["EventOccurrence"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventOccurrence.sort_order"
    )


class RaceType(Base):
    """
    Standardised (sport, distance) category shared across every event that offers
    it, e.g. "42.195 km", "26.22 miles" and "Marathon" on three different
    organisers' pages should all resolve to the SAME row here (label
    "running_marathon") rather than three unrelated free-text strings - see
    race_types.get_or_create_race_type(), which every EventDistance is resolved
    through instead of each one inserting its own copy.

    label is the canonical `{sport}_{distance_category}` slug (e.g.
    "running_marathon", "running_10_k", "running_10_m") and is what get_or_create
    looks rows up by; sport/distance_category are kept as their own columns too so
    they're queryable without parsing the label back apart.
    """

    __tablename__ = "race_types"
    __table_args__ = (UniqueConstraint("label", name="uq_race_type_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    sport: Mapped[Sport] = mapped_column(Enum(Sport, name="race_sport"))
    # e.g. "marathon", "half_marathon", "10k", "10_k", "10_m" - see llm_extractor.py's
    # distance_category field for exactly what values this can take and why.
    distance_category: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    distances: Mapped[list["EventDistance"]] = relationship(back_populates="race_type")


class EventDistance(Base):
    """One distance option on an event (e.g. "10k", "Half Marathon"), with that distance's own price, if stated."""

    __tablename__ = "event_distances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    distance_text: Mapped[str] = mapped_column(String(255))
    price_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Null when the extractor couldn't confidently categorise this distance (e.g. no
    # sport known, or the LLM left distance_category blank) - distance_text/price_text
    # (the verbatim page text) are still stored either way.
    race_type_id: Mapped[int | None] = mapped_column(ForeignKey("race_types.id"), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="distances")
    race_type: Mapped["RaceType | None"] = relationship(back_populates="distances")


class EventOccurrence(Base):
    """
    One concrete, known date+time this event happens - see Occurrence's own
    docstring for which events get rows here at all (a one-off event, exactly
    one row; a bounded/enumerated recurring event like atwevents.co.uk's own
    per-session tickets, one row per listed date) versus which don't (an
    unbounded recurrence like parkrun's "every Saturday, forever", nothing to
    enumerate - see Event.occurrence_weekdays/occurrence_time instead).

    Mirrors EventDistance's own shape closely (one-to-many off Event, own
    sort_order, verbatim text alongside a price override) - same underlying
    pattern, "distance" there vs. "date" here.
    """

    __tablename__ = "event_occurrences"
    __table_args__ = (
        UniqueConstraint("event_id", "starts_at", name="uq_event_occurrence_event_starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Verbatim, as written on the page (e.g. "18th Aug 2026", "06:00 PM") - same
    # "never let a derived value replace the original" convention as
    # Event.summary/summary_alt and EventDistance.distance_text.
    date_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time_text: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Per-occurrence price override, if the page states one (confirmed in practice:
    # atwevents.co.uk's swim sessions each have their own price, e.g. "on the day"
    # vs "pre-booking" vs a monthly pass). Null means nothing occurrence-specific
    # was stated, not "this occurrence is free".
    price_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The booking platform's own stable per-ticket/session id, when the page exposes
    # one (confirmed in practice: eventrac's own rId=... query param on its "Enter
    # Now" buttons) - lets a re-crawl update this exact row in place instead of the
    # delete-everything-reinsert-everything EventDistance has to do (distance_text
    # has no stable key across re-wordings; a specific calendar date does, so this is
    # a nice-to-have upgrade over that, not a requirement - re-crawl still falls back
    # to matching on (event_id, starts_at) via the unique constraint above when a
    # platform doesn't expose one).
    external_ticket_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="occurrences")


class CrawlRun(Base):
    """Audit log of every listing-crawl / event-crawl attempt, success or not."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[CrawlRunType] = mapped_column(Enum(CrawlRunType, name="crawl_run_type"))
    target_url: Mapped[str] = mapped_column(String(1024))
    organiser_id: Mapped[int | None] = mapped_column(ForeignKey("organisers.id"), nullable=True)
    status: Mapped[CrawlStatus] = mapped_column(Enum(CrawlStatus, name="crawl_status"))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
