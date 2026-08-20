"""
Organiser row - one real-world event host (or, for a PLATFORM row, one
umbrella source like parkrun - see feed_importers.get_or_create_organiser).

`source_type` is the enforcement point for the "never store aggregator/
platform data" rule: only rows with source_type == "organiser" are ever
enqueued for event crawling (see listing_crawler.py). Aggregators are only
ever a *source of organiser URLs* (phase 2), never a source of event rows.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .enums import SourceType

__all__ = ["Organiser"]


class Organiser(Base):
    __tablename__ = "organisers"
    __table_args__ = (UniqueConstraint("homepage_url", name="uq_organiser_homepage_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    homepage_url: Mapped[str] = mapped_column(String(1024))
    # Several organisers split listings across multiple sub-pages (e.g. by
    # category), so this is a list rather than a single URL. Empty/absent
    # until discover_listing_urls() (see llm/listing_extraction.py) has run once.
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
    # URL (see admin/seed_organisers.py: kept as its own flat "sitemap_url" column in
    # the seed CSV itself, specifically so admin/discover_sitemaps.py - a plain
    # csv.DictWriter script - never has to parse/merge JSON just to set one string;
    # only merged into this dict at seed time), or "parkrun"'s own country_code.
    # Each handler is responsible for reading whatever keys it expects out of this,
    # with its own sensible defaults - deliberately not a per-handler schema, since
    # this is expected to stay a small, hand-maintained set of special cases.
    handler_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Who/what is responsible for this organiser's data being collected at all - see
    # scraping/robots.py's is_allowed(): "bot" (the default, and what organisers_seed.csv
    # seeds every row with) means an unattended automated crawl, which must respect
    # robots.txt/a site's stated crawling policy. Any other value names a real person who
    # has separately obtained the site owner's own permission to collect this data outside
    # what robots.txt alone would allow - see README.md's "registrator" section for why
    # this exists and the parkrun handler's own registrator override in particular.
    # Propagated onto every Event/EventDistance/EventOccurrence this organiser's crawl
    # produces (see pattern_site/event_crawler.py), not just kept here, so each row's own
    # provenance is self-contained and doesn't require joining back to organisers to know
    # it. server_default for the same _add_missing_columns auto-migration reasoning as
    # handler above.
    registrator: Mapped[str] = mapped_column(String(64), default="bot", server_default="bot")

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
