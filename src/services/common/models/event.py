"""
Event and its two child tables (EventDistance, EventOccurrence).

`content_hash` lets a re-crawl skip re-extraction (and therefore the LLM
call) when a page hasn't changed since last time.
"""

from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .enums import EventLifecycle, EventStatus, Occurrence, RegistrationStatus

__all__ = ["Event", "EventDistance", "EventOccurrence"]


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("url", name="uq_event_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organiser_id: Mapped[int] = mapped_column(ForeignKey("organisers.id"))
    url: Mapped[str] = mapped_column(String(1024))

    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Original summary, as extracted - either LLM-rephrased from the page's own markdown,
    # or (see scraping/structured_data.py) read verbatim from the page's own schema.org
    # JSON-LD description when present.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-rewritten alternative wording of `summary` above (see
    # llm/event_extraction.rewrite_summary) - genuinely reworded, not a close paraphrase,
    # so what gets stored/republished (e.g. admin/export's HTML export) never has to be
    # another site's own copy verbatim.
    summary_alt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-condensed single-sentence summary of `summary` above (see llm/event_extraction.rewrite_summary).
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

    # See RegistrationStatus - unlike occurrence/status below, this deliberately does NOT
    # default to the "most common" value: whether entry is even required, let alone
    # currently open, genuinely isn't stated on enough pages to assume one way or the
    # other (unlike occurrence, where "one-off" really is the overwhelmingly common case).
    # server_default (not just the Python-side default= below) so this NOT NULL column can
    # still be added to an already-existing `events` table by db.py's _add_missing_columns -
    # same reasoning as Event.occurrence's own server_default.
    registration_status: Mapped[RegistrationStatus] = mapped_column(
        Enum(
            RegistrationStatus,
            name="registration_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=RegistrationStatus.UNKNOWN,
        server_default=RegistrationStatus.UNKNOWN.value,
    )
    # The page's own wording about registration/entry opening, closing, or current status,
    # verbatim - same "never let a derived value replace the original" convention as
    # date_text/age_restriction_text above. Confirmed in practice: zigzagrunning.co.uk's
    # Two Hundred Miles Challenge states just "Registration is Closed", nothing else - there
    # isn't always a date/time to parse out of this at all, so this raw fallback matters on
    # its own, not just as a debugging aid alongside the two columns below.
    registration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Parsed date+time entries open/close, when the page actually states them (e.g. "Entries
    # open 9am, 1 March 2026") - null whenever only a status/no detail at all was given, same
    # spirit as EventOccurrence.starts_at (a single combined instant, not separate date/time
    # columns, since - unlike Event.occurrence_time - there's no recurring weekday rule to
    # combine it with here).
    registration_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # See EventLifecycle - defaults to SCHEDULED since (unlike registration_status above)
    # silence really does mean "going ahead". server_default for the same
    # _add_missing_columns auto-migration reasoning as registration_status/occurrence.
    lifecycle_status: Mapped[EventLifecycle] = mapped_column(
        Enum(
            EventLifecycle,
            name="event_lifecycle",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=EventLifecycle.SCHEDULED,
        server_default=EventLifecycle.SCHEDULED.value,
    )
    # The page's own wording about a cancellation/postponement, verbatim - same rationale as
    # registration_text: often a reason or a rescheduled date that doesn't reduce to a single
    # structured field ("Cancelled due to adverse weather", "Postponed to 12 Sept 2026").
    # Null whenever lifecycle_status is SCHEDULED.
    lifecycle_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # See EventStatus - defaults to VALID since most crawled URLs really are events;
    # pattern_site/event_crawler.py sets this from the LLM's own is_valid_event/
    # invalid_reason verdict (see llm/event_extraction.py) rather than us guessing from
    # empty fields.
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
        # everywhere else this enum's string form is used - llm/event_extraction's
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
    # same priority admin/export's html_export._render_map already uses) via
    # events/geocoding_client.py, once per crawl - never looked up at query time. Null
    # until a crawl has actually attempted geocoding (or attempted and found nothing) -
    # there's no third "not yet tried" state distinct from "tried, no result", since
    # a failed/empty geocode isn't retried differently from never having tried at all.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # See Organiser.registrator's own docstring - set from the owning organiser's current
    # registrator at crawl time (pattern_site/event_crawler.py), refreshed on every
    # re-crawl rather than only at creation, so this always reflects who/what is CURRENTLY
    # responsible for this row's data, not a stale value from whenever it first appeared.
    registrator: Mapped[str] = mapped_column(String(64), default="bot", server_default="bot")

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

    # See Organiser.registrator's own docstring - same value as the owning Event's own
    # registrator at the time this row was (re-)written.
    registrator: Mapped[str] = mapped_column(String(64), default="bot", server_default="bot")

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

    # See Organiser.registrator's own docstring - same value as the owning Event's own
    # registrator at the time this row was (re-)written.
    registrator: Mapped[str] = mapped_column(String(64), default="bot", server_default="bot")

    event: Mapped["Event"] = relationship(back_populates="occurrences")
